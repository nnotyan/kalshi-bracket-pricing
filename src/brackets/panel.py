"""Turning raw snapshots into an aligned, annotated quote panel.

Three things happen here, and the second one is the one that matters most.

**Inversion.** Every row gets its derived asks, mid, and half-spread from
:mod:`brackets.book`.

**Alignment.** A bracket is only tradeable as a bracket if every leg is quoted
at the same instant. A poll that returns eleven of twelve legs, because one
series call timed out or because a market flipped to ``closed`` halfway through
the sweep, produces a cross-section that never existed. Summing it against par
manufactures an arbitrage out of nothing more than a dropped request. So the
panel is cut down to event-minutes whose leg set exactly matches the leg set the
structural classifier signed off on, and the discarded minutes are counted
rather than silently dropped.

**Staleness.** The panel records, for every leg at every minute, how long its
two bid sides have been unchanged, and how deep the offer behind the derived ask
is. Neither is a price, and neither shows up in an overround calculation, but
between them they explain most of what a naive screen reports as free money: a
leg whose bid has not moved in forty minutes is not quoting a price, it is
displaying an old one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .book import add_derived_quotes

# Columns the screen needs from a prepared panel.
_KEY = ["event_ticker", "ticker"]


def prepare(panel: pd.DataFrame) -> pd.DataFrame:
    """Invert the book and attach per-leg staleness and depth annotations."""
    frame = panel.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values(["ticker", "ts"])
    frame = add_derived_quotes(frame)

    frame["stale_minutes"] = _stale_minutes(frame)
    frame["quote_changed"] = frame.groupby("ticker", sort=False)["stale_minutes"].transform(
        lambda s: s.eq(0))
    frame["thin_offer"] = frame["yes_ask_size"].astype("float64").fillna(0.0)
    return frame.sort_values(["ts", "event_ticker", "ticker"]).reset_index(drop=True)


def _stale_minutes(frame: pd.DataFrame) -> pd.Series:
    """Minutes since either bid side of a leg last moved.

    Measured in wall-clock minutes rather than in polls, so a gap in collection
    counts as elapsed time. That is the conservative reading: if the collector
    was not looking, we do not get to claim the quote was fresh.
    """
    parts = []
    for _, group in frame.groupby("ticker", sort=False):
        bids = group[["yes_bid", "no_bid"]].astype("float64").fillna(-1.0)
        changed = bids.ne(bids.shift()).any(axis=1).to_numpy(copy=True)
        changed[0] = True
        stamps = group["ts"].to_numpy()
        last_change = np.maximum.accumulate(np.where(changed, np.arange(len(group)), -1))
        elapsed = (stamps - stamps[last_change]) / np.timedelta64(1, "m")
        parts.append(pd.Series(elapsed, index=group.index))
    return pd.concat(parts).reindex(frame.index)


def align(panel: pd.DataFrame, expected: dict[str, list[str]],
          require_active: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep only event-minutes whose leg set is complete.

    ``expected`` maps an event ticker to the legs the classifier proved cover the
    outcome space. Returns the kept panel and a per-event report of how many
    minutes were dropped and why, because the drop rate is itself a data-quality
    signal: an event that is regularly incomplete is one whose legs are being
    listed and delisted underneath the screen.
    """
    frame = panel
    if require_active:
        active = frame["status"].astype("string").str.lower().isin(
            ["active", "open", "initialized"])
        frame = frame[active.fillna(True)]

    keep_rows = []
    report = []
    for event_ticker, group in frame.groupby("event_ticker", sort=False):
        wanted = set(expected.get(str(event_ticker), []))
        if not wanted:
            report.append({"event_ticker": event_ticker, "minutes": 0, "complete": 0,
                           "dropped": int(group["ts"].nunique()), "reason": "not in universe"})
            continue
        group = group[group["ticker"].isin(wanted)]
        per_minute = group.groupby("ts")["ticker"].nunique()
        complete = per_minute[per_minute == len(wanted)].index
        keep_rows.append(group[group["ts"].isin(complete)])
        report.append({
            "event_ticker": event_ticker,
            "minutes": int(per_minute.size),
            "complete": int(len(complete)),
            "dropped": int(per_minute.size - len(complete)),
            "reason": "incomplete cross-section",
        })

    kept = (pd.concat(keep_rows, ignore_index=True) if keep_rows
            else frame.iloc[0:0].copy())
    return kept, pd.DataFrame(report)


def event_quality(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-event quoting quality, measured over the whole sample.

    Used to decide which events are *competitively quoted*, which is a property
    of how an event is normally made rather than of any one minute. Keeping the
    two apart is what makes the triage in :mod:`brackets.diagnostics`
    non-circular: tightness classifies the event, staleness classifies the
    minute.
    """
    grouped = panel.groupby("event_ticker", sort=False)
    quality = grouped.agg(
        minutes=("ts", "nunique"),
        legs=("ticker", "nunique"),
        median_width=("width", "median"),
        p90_width=("width", lambda s: float(np.nanpercentile(s.astype("float64"), 90))),
        median_offer_size=("yes_ask_size", "median"),
        median_stale=("stale_minutes", "median"),
        p90_stale=("stale_minutes", lambda s: float(np.nanpercentile(s.astype("float64"), 90))),
        offered_share=("yes_offered", "mean"),
        volume_24h=("volume_24h", "max"),
    ).reset_index()
    return quality


def mark_competitive(quality: pd.DataFrame, max_median_width: float = 2.0,
                     min_offer_size: float = 25.0,
                     min_offered_share: float = 0.99) -> pd.DataFrame:
    """Label events that are genuinely two-sided and tight most of the time.

    The three conditions are independent failure modes. A wide book is not
    mispriced, it is just wide. A book with no depth behind the offer has a price
    you cannot trade in size. A book that is one-sided part of the time has legs
    that periodically cannot be bought at all.
    """
    out = quality.copy()
    out["competitive"] = (
        (out["median_width"] <= max_median_width)
        & (out["median_offer_size"].fillna(0.0) >= min_offer_size)
        & (out["offered_share"].fillna(0.0) >= min_offered_share)
    )
    return out
