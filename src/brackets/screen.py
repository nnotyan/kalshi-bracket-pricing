"""The screens themselves: sum against par, and check a ladder for monotonicity.

Two pieces of arithmetic do most of the work.

The first is where the no-arbitrage band comes from. Write each leg's derived
ask as its mid plus its half-spread, ``a_i = m_i + s_i``, and each bid as
``b_i = m_i - s_i``. Buying the whole bracket costs ``sum a_i = sum m_i + sum s_i``
and pays a dollar, so it wins only when ``sum m_i < 100 - sum s_i``. Selling the
whole bracket (which on Kalshi means buying one NO on every leg; exactly one YES
settles, so ``n - 1`` NOs pay) returns ``sum b_i`` against the same dollar, so it
wins only when ``sum m_i > 100 + sum s_i``. The two conditions bracket the mid
sum inside

    100 - sum(s_i)   <=   sum(m_i)   <=   100 + sum(s_i)

and nothing inside that band is tradeable at any size. The half-width is the sum
of the per-leg half-spreads, so it grows with the number of legs. A six-leg
weather bracket quoted a penny wide on every leg has a band three cents wide; a
twenty-eight-leg crypto bracket quoted the same way has a band fourteen cents
wide. The quoting quality is identical but the tolerance for an apparent
overround is very different, which is why a raw flag has to be read relative to
the band rather than against zero.

The second is what a ladder does instead. Nested thresholds are not constrained
to sum to anything, so the sum-against-par screen is meaningless on them. What is
constrained is the ordering. If a higher threshold can be sold for more than a
lower threshold costs (``bid(t_high) > ask(t_low)`` with ``t_high > t_low``),
then buying the low rung and selling the high one locks in the difference: the
pair pays at least a dollar in every state and one hundred cents more in the
band between the two strikes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .book import PAR_CENTS
from .fees import DEFAULT_FEE_RATE, bracket_fee_cents, gini_simpson
from .structure import EventStructure, Shape

DEFAULT_SIZES = (1, 10, 50, 250, 1000)


@dataclass(frozen=True)
class ScreenConfig:
    fee_rate: float = DEFAULT_FEE_RATE
    order_sizes: Sequence[int] = DEFAULT_SIZES
    min_offer_size: float = 25.0
    max_stale_minutes: float = 5.0


def _cross_section(group: pd.DataFrame) -> dict:
    """Reduce one event-minute to the quantities both screens are built from.

    Depth is recorded separately for the two sides, because they are not the
    same trade. Buying the bracket lifts each leg's YES offer, which is the NO
    book, so it needs ``yes_ask_size``. Selling the bracket buys a NO on every
    leg, which means hitting each leg's YES bid, so it needs ``yes_bid_size``. A
    leg nobody bids for can still be bought, and a leg nobody offers can still
    be sold; checking one side's depth against the other side's trade is how a
    screen ends up reporting an unexecutable bracket as free money.
    """
    ask = group["yes_ask_derived"].to_numpy(dtype="float64")
    bid = group["yes_bid"].to_numpy(dtype="float64")
    half = group["half_spread"].to_numpy(dtype="float64")
    mid = group["mid"].to_numpy(dtype="float64")
    ask_size = group["yes_ask_size"].to_numpy(dtype="float64")
    bid_size = group["yes_bid_size"].to_numpy(dtype="float64")
    stale = group["stale_minutes"].to_numpy(dtype="float64")

    def smallest(values: np.ndarray) -> float:
        if not len(values) or np.all(np.isnan(values)):
            return 0.0
        return float(np.nanmin(values))

    return {
        "n_legs": len(group),
        "ask_sum": float(np.sum(ask)),
        "bid_sum": float(np.sum(bid)),
        "mid_sum": float(np.sum(mid)),
        "band": float(np.sum(half)),
        "long_min_size": smallest(ask_size),
        "short_min_size": smallest(bid_size),
        "long_all_offered": bool(group["yes_offered"].all()),
        "short_all_offered": bool(group["no_offered"].all()),
        "max_stale": float(np.nanmax(stale)) if len(stale) else 0.0,
        "any_crossed": bool(group["crossed"].any()),
        "_asks": ask,
        "_bids": bid,
    }


def screen_partitions(panel: pd.DataFrame, structures: Mapping[str, EventStructure],
                      config: ScreenConfig | None = None,
                      require_proven: bool = True) -> pd.DataFrame:
    """Price the long and short bracket at every event-minute.

    One row per event-minute. The gross columns are pure quote arithmetic; the
    ``net_{size}`` columns subtract the actual fee the venue would charge for a
    clip of that size, which is where the per-order round-up starts to matter.
    """
    config = config or ScreenConfig()
    rows: list[dict] = []

    for (event_ticker, stamp), group in panel.groupby(["event_ticker", "ts"], sort=False):
        structure = structures.get(str(event_ticker))
        if structure is None:
            continue
        if require_proven and not structure.screenable:
            continue

        stats = _cross_section(group)
        asks = stats.pop("_asks")
        bids = stats.pop("_bids")

        long_gross = PAR_CENTS - stats["ask_sum"]
        # Selling the bracket means buying one NO on every leg. Exactly one YES
        # settles, so n-1 NOs pay a dollar each, and the profit reduces to the
        # bid sum less par.
        short_gross = stats["bid_sum"] - PAR_CENTS
        no_asks = PAR_CENTS - bids

        row = {
            "event_ticker": event_ticker,
            "ts": stamp,
            "shape": structure.shape.value,
            "exhaustive": structure.exhaustive.value,
            **stats,
            "long_gross": long_gross,
            "short_gross": short_gross,
            # An edge only means something next to the band it has to clear.
            "long_band_ratio": long_gross / stats["band"] if stats["band"] > 0 else np.nan,
            "short_band_ratio": short_gross / stats["band"] if stats["band"] > 0 else np.nan,
            "gini_simpson": gini_simpson(asks),
        }

        for size in config.order_sizes:
            long_fee = bracket_fee_cents(size, asks, rate=config.fee_rate)
            short_fee = bracket_fee_cents(size, no_asks, rate=config.fee_rate)
            row[f"long_fee_{size}"] = long_fee / size
            row[f"short_fee_{size}"] = short_fee / size
            row[f"long_net_{size}"] = long_gross - long_fee / size
            row[f"short_net_{size}"] = short_gross - short_fee / size

        rows.append(row)

    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.sort_values(["ts", "event_ticker"]).reset_index(drop=True)
    return frame


def naive_flags(screened: pd.DataFrame, sizes: Sequence[int] = DEFAULT_SIZES) -> pd.DataFrame:
    """Every event-minute a screen that ignores structure would call an arbitrage.

    The raw flag is the starting point. It uses only the quotes, applies no
    depth or staleness filter, and does not ask whether the legs cover the
    outcome space. Counting these flags first makes the later filtering
    auditable, since every flag that is removed has to be accounted for.
    """
    if not len(screened):
        return screened
    flags = screened[(screened["long_gross"] > 0) | (screened["short_gross"] > 0)].copy()
    long = flags["long_gross"] > 0
    flags["side"] = np.where(long, "long", "short")
    flags["gross"] = np.where(long, flags["long_gross"], flags["short_gross"])
    flags["band_ratio"] = np.where(long, flags["long_band_ratio"],
                                   flags["short_band_ratio"])
    # Collapse the side-specific execution columns onto the side that flagged, so
    # the triage never has to know which trade it is looking at.
    flags["min_offer_size"] = np.where(long, flags["long_min_size"],
                                       flags["short_min_size"])
    flags["all_offered"] = np.where(long, flags["long_all_offered"],
                                    flags["short_all_offered"])
    for size in sizes:
        flags[f"net_{size}"] = np.where(long, flags[f"long_net_{size}"],
                                        flags[f"short_net_{size}"])
    return flags.reset_index(drop=True)


def screen_ladders(panel: pd.DataFrame, structures: Mapping[str, EventStructure],
                   config: ScreenConfig | None = None) -> pd.DataFrame:
    """Monotonicity violations across adjacent rungs of a threshold ladder.

    For an upward ladder the implied probability must fall as the threshold
    rises. A violation is tradeable when the higher rung's bid sits above the
    lower rung's ask, because the pair then costs less than the dollar it is
    guaranteed to return. Only adjacent rungs are checked: a violation between
    two distant rungs implies one between some adjacent pair, so the adjacent
    sweep is complete and linear rather than quadratic.
    """
    config = config or ScreenConfig()
    rows: list[dict] = []

    for (event_ticker, stamp), group in panel.groupby(["event_ticker", "ts"], sort=False):
        structure = structures.get(str(event_ticker))
        if structure is None or structure.shape is not Shape.LADDER:
            continue

        order = {leg.ticker: (leg.lo if np.isinf(leg.hi) else leg.hi)
                 for leg in structure.legs}
        group = group.assign(_threshold=group["ticker"].map(order)).dropna(subset=["_threshold"])
        group = group.sort_values("_threshold")

        thresholds = group["_threshold"].to_numpy(dtype="float64")
        asks = group["yes_ask_derived"].to_numpy(dtype="float64")
        bids = group["yes_bid"].to_numpy(dtype="float64")
        ask_sizes = group["yes_ask_size"].to_numpy(dtype="float64")
        bid_sizes = group["yes_bid_size"].to_numpy(dtype="float64")
        stale = group["stale_minutes"].to_numpy(dtype="float64")
        tickers = group["ticker"].to_numpy()

        for i in range(len(group) - 1):
            gross = bids[i + 1] - asks[i]
            if gross <= 0:
                continue
            legs = [asks[i], PAR_CENTS - bids[i + 1]]
            row = {
                "event_ticker": event_ticker, "ts": stamp,
                "low_ticker": tickers[i], "high_ticker": tickers[i + 1],
                "low_threshold": thresholds[i], "high_threshold": thresholds[i + 1],
                "low_ask": asks[i], "high_bid": bids[i + 1],
                "gross": gross,
                # The low rung is bought and the high rung is sold, so the two
                # legs are constrained by depth on opposite sides of the book.
                "min_offer_size": float(np.nanmin([ask_sizes[i], bid_sizes[i + 1]])),
                "max_stale": float(np.nanmax(stale[i:i + 2])),
            }
            for size in config.order_sizes:
                fee = bracket_fee_cents(size, legs, rate=config.fee_rate)
                row[f"net_{size}"] = gross - fee / size
            rows.append(row)

    return pd.DataFrame(rows)
