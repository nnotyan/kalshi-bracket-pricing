"""Building the minute-resolution quote panel.

Kalshi does not hand out a historical top-of-book series on the public API, so
the panel is built by polling. Once a minute the collector pulls every event in
the working universe with its markets nested, normalises the prices to cents and
appends one row per market per minute. The result is the same shape a
minute-bar file would have, except that every field is a snapshot of the book
rather than a summary of trades, which is what a spread-and-fee study needs:
trades tell you where the last taker crossed, quotes tell you where the next one
can.

Polling by series rather than by event keeps the request count down. A series
fetch returns every open event under it in one call, so a universe of forty
events spread over a dozen series costs a dozen requests a minute rather than
forty.

Discovery is a separate step and is deliberately cheap to re-run. It walks the
open events, classifies each one, and writes out the subset worth watching along
with the classification, so the screen never has to re-derive structure from a
quote panel that has no strike fields in it.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .api import KalshiClient
from .structure import Exhaustive, EventStructure, Leg, Shape, classify

log = logging.getLogger(__name__)

# One row per market per poll. Anything not in here is either derivable from
# these or is metadata that belongs in the universe file instead.
QUOTE_COLUMNS = [
    "ts", "event_ticker", "series_ticker", "ticker",
    "yes_bid", "no_bid", "yes_ask", "no_ask",
    "yes_bid_size", "yes_ask_size",
    "last_price", "volume", "volume_24h", "open_interest", "liquidity", "status",
]


@dataclass
class UniverseEntry:
    """One event kept for collection, with the structural verdict attached."""

    event_ticker: str
    series_ticker: str
    title: str
    shape: str
    exhaustive: str
    n_legs: int
    screenable: bool
    support: str
    evidence: list[str]
    venue_mutually_exclusive: bool | None
    venue_collateral_type: str | None
    liquidity: float
    volume_24h: float
    close_time: str
    tickers: list[str]
    # Leg intervals travel with the universe so that the screen never has to
    # re-derive structure from a quote panel, which carries no strike fields.
    # Unbounded ends are written as null because JSON has no infinity.
    leg_bounds: list[dict] = field(default_factory=list)

    def to_structure(self) -> EventStructure:
        """Rebuild the classifier's verdict without re-fetching the event."""
        legs = [Leg(ticker=row["ticker"],
                    lo=-math.inf if row["lo"] is None else float(row["lo"]),
                    hi=math.inf if row["hi"] is None else float(row["hi"]),
                    label=row.get("label", ""))
                for row in self.leg_bounds]
        return EventStructure(
            event_ticker=self.event_ticker,
            shape=Shape(self.shape),
            exhaustive=Exhaustive(self.exhaustive),
            legs=legs,
            support=None,
            evidence=list(self.evidence),
            venue_mutually_exclusive=self.venue_mutually_exclusive,
            venue_collateral_type=self.venue_collateral_type,
        )


def _event_liquidity(event: dict) -> tuple[float, float]:
    markets = event.get("markets") or []
    liquidity = sum(float(m.get("liquidity") or 0.0) for m in markets)
    volume = sum(float(m.get("volume_24h") or 0.0) for m in markets)
    return liquidity, volume


def discover(client: KalshiClient, series: Sequence[str] | None = None,
             min_legs: int = 3, min_volume: float = 0.0,
             shapes: Iterable[str] = ("partition", "ladder"),
             max_pages: int = 60,
             domain_bounds: dict[str, tuple[float | None, float | None]] | None = None,
             strict_support: bool = False) -> list[UniverseEntry]:
    """Classify open events and keep the ones worth watching.

    With no ``series`` filter this walks the whole open board, which is a few
    thousand markets and takes a minute or two. Passing a series list turns it
    into a targeted refresh.
    """
    wanted = {Shape(s) for s in shapes}
    events: list[dict] = []
    if series:
        for ticker in series:
            events.extend(client.events(series_ticker=ticker, max_pages=max_pages))
    else:
        events = client.events(max_pages=max_pages)

    entries: list[UniverseEntry] = []
    for event in events:
        markets = event.get("markets") or []
        if len(markets) < min_legs:
            continue
        structure = classify(event, domain_bounds=domain_bounds,
                             strict_support=strict_support)
        if structure.shape not in wanted:
            continue
        liquidity, volume = _event_liquidity(event)
        if volume < min_volume:
            continue
        entries.append(UniverseEntry(
            event_ticker=structure.event_ticker,
            series_ticker=str(event.get("series_ticker") or ""),
            title=str(event.get("title") or ""),
            shape=structure.shape.value,
            exhaustive=structure.exhaustive.value,
            n_legs=structure.n_legs,
            screenable=structure.screenable,
            support=structure.support.kind if structure.support else "",
            evidence=list(structure.evidence),
            venue_mutually_exclusive=structure.venue_mutually_exclusive,
            venue_collateral_type=structure.venue_collateral_type,
            liquidity=liquidity,
            volume_24h=volume,
            close_time=str(markets[0].get("close_time") or ""),
            tickers=[leg.ticker for leg in structure.legs],
            leg_bounds=[{"ticker": leg.ticker,
                         "lo": None if math.isinf(leg.lo) else leg.lo,
                         "hi": None if math.isinf(leg.hi) else leg.hi,
                         "label": leg.label}
                        for leg in structure.legs],
        ))

    entries.sort(key=lambda e: (-e.volume_24h, -e.liquidity))
    return entries


def write_universe(entries: Sequence[UniverseEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(entries),
        "events": [asdict(e) for e in entries],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_universe(path: Path) -> list[UniverseEntry]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [UniverseEntry(**row) for row in payload["events"]]


def _snapshot_rows(events: Sequence[dict], keep: set[str], stamp: pd.Timestamp) -> list[dict]:
    rows = []
    for event in events:
        if event.get("event_ticker") not in keep:
            continue
        series = event.get("series_ticker") or ""
        for market in event.get("markets") or []:
            rows.append({
                "ts": stamp,
                "event_ticker": event.get("event_ticker"),
                "series_ticker": series,
                "ticker": market.get("ticker"),
                "yes_bid": market.get("yes_bid"),
                "no_bid": market.get("no_bid"),
                "yes_ask": market.get("yes_ask"),
                "no_ask": market.get("no_ask"),
                "yes_bid_size": market.get("yes_bid_size"),
                "yes_ask_size": market.get("yes_ask_size"),
                "last_price": market.get("last_price"),
                "volume": market.get("volume"),
                "volume_24h": market.get("volume_24h"),
                "open_interest": market.get("open_interest"),
                "liquidity": market.get("liquidity"),
                "status": market.get("status"),
            })
    return rows


def poll_once(client: KalshiClient, entries: Sequence[UniverseEntry]) -> pd.DataFrame:
    """One snapshot of the whole universe, stamped to the minute it was taken."""
    stamp = pd.Timestamp.now(tz="UTC").floor("min")
    keep = {e.event_ticker for e in entries}
    by_series: dict[str, None] = {}
    for entry in entries:
        by_series.setdefault(entry.series_ticker, None)

    rows: list[dict] = []
    for series in by_series:
        try:
            events = client.events(series_ticker=series, max_pages=10)
        except Exception as exc:  # a single bad series must not stop the poll
            log.warning("series %s failed this poll: %s", series, exc)
            continue
        rows.extend(_snapshot_rows(events, keep, stamp))

    frame = pd.DataFrame(rows, columns=QUOTE_COLUMNS)
    return frame


def collect(client: KalshiClient, entries: Sequence[UniverseEntry], out_dir: Path,
            minutes: int = 60, interval: float = 60.0, flush_every: int = 5) -> Path:
    """Poll the universe once a minute and append the panel to parquet.

    Files are cut by hour so that a long run stays readable and a crash costs at
    most one flush interval. Sleeping is measured against the intended schedule
    rather than against the end of the previous poll, so a slow poll does not
    push every later stamp off the minute grid.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    buffer: list[pd.DataFrame] = []
    written: Path | None = None
    start = time.monotonic()

    for step in range(minutes):
        frame = poll_once(client, entries)
        if len(frame):
            buffer.append(frame)
        log.info("poll %d/%d: %d quotes", step + 1, minutes, len(frame))

        if buffer and (len(buffer) >= flush_every or step == minutes - 1):
            written = _flush(buffer, out_dir)
            buffer = []

        if step < minutes - 1:
            target = start + (step + 1) * interval
            time.sleep(max(0.0, target - time.monotonic()))

    if buffer:
        written = _flush(buffer, out_dir)
    return written or out_dir


def _flush(buffer: list[pd.DataFrame], out_dir: Path) -> Path:
    frame = pd.concat(buffer, ignore_index=True)
    hour = pd.Timestamp(frame["ts"].iloc[0]).strftime("%Y%m%dT%H")
    path = out_dir / f"quotes_{hour}.parquet"
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame], ignore_index=True)
        frame = frame.drop_duplicates(subset=["ts", "ticker"], keep="last")
    frame.to_parquet(path, index=False)
    log.info("wrote %d rows to %s", len(frame), path.name)
    return path


def load_panel(path: Path) -> pd.DataFrame:
    """Read one parquet file or a directory of them into a single panel."""
    path = Path(path)
    files = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"no parquet files under {path}")
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame.drop_duplicates(subset=["ts", "ticker"], keep="last").sort_values(["ts", "ticker"])
