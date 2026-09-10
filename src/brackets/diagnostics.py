"""Why a raw flag is almost never money.

A screen that only does arithmetic produces flags. Most of them are artefacts,
and the useful output of this project is not the flag count but the breakdown of
where the flags came from. Each raw flag is charged to the first of the
following that applies, in this order:

``wrong_shape``
    The legs were summed against par but they do not form a partition at all.
    Nested ladder rungs are the usual culprit and they are spectacular: summing
    eighty nested thresholds produces an apparent short-side edge in the tens of
    dollars.

``not_exhaustive``
    The legs are disjoint but leave outcomes unlisted, so the implied
    probabilities have no reason to sum to one. Buying the lot below par is a
    bet that the outcome lands inside the listed range, not an arbitrage. This
    is the quiet one: the arithmetic looks exactly like a real edge.

``crossed_book``
    Some leg has bids on both sides adding to more than a dollar. That cannot
    exist on a live venue, so the snapshot is internally inconsistent and every
    number derived from it is suspect.

``stale_quote``
    Some leg has not moved either bid in longer than the staleness tolerance.
    An old quote is not a price. This is by far the most common cause on tightly
    quoted events, because the whole bracket only has to be wrong by a cent or
    two to clear a band that is itself only a cent or two wide.

``no_depth``
    Every leg is quoted and fresh, but at least one is too thin on the side the
    trade actually needs -- the offer for a bracket being bought, the bid for one
    being sold. The price is real and the size is not. Legs with nothing at all
    on the relevant side land here too, since a depth of zero is the limiting
    case of a quote you cannot trade against.

``fee_bound``
    Everything above passes and the gross edge is genuine, but it does not
    survive the venue's fee at any order size on the ladder. This is the
    interesting failure: a real dislocation that is simply too small to pay for.

``survives``
    Net of spread, depth, staleness and fees, still positive at some size.

The ordering matters because a flag usually fails several tests at once, and
attributing it to the first structural reason rather than to the last numerical
one is what stops the summary from blaming fees for what was actually a ladder.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .screen import DEFAULT_SIZES, ScreenConfig
from .structure import EventStructure, Exhaustive, Shape

REASONS = ("wrong_shape", "not_exhaustive", "crossed_book", "stale_quote",
           "no_depth", "fee_bound", "survives")


def triage(flags: pd.DataFrame, structures: Mapping[str, EventStructure],
           config: ScreenConfig | None = None,
           sizes: Sequence[int] = DEFAULT_SIZES) -> pd.DataFrame:
    """Charge every raw flag to a cause."""
    config = config or ScreenConfig()
    if not len(flags):
        out = flags.copy()
        out["reason"] = pd.Series(dtype="object")
        return out

    out = flags.copy()
    shapes = out["event_ticker"].map(
        lambda t: structures[t].shape if t in structures else None)
    exhaustive = out["event_ticker"].map(
        lambda t: structures[t].exhaustive if t in structures else None)

    net_cols = [f"net_{s}" for s in sizes if f"net_{s}" in out.columns]
    best_net = out[net_cols].max(axis=1) if net_cols else pd.Series(0.0, index=out.index)
    out["best_net"] = best_net
    out["best_size"] = (out[net_cols].idxmax(axis=1).str.replace("net_", "", regex=False)
                        .astype(int) if net_cols else np.nan)

    reason = pd.Series("survives", index=out.index, dtype="object")
    reason = reason.mask(best_net <= 0, "fee_bound")
    reason = reason.mask(out["min_offer_size"].fillna(0.0) < config.min_offer_size, "no_depth")
    reason = reason.mask(out["max_stale"].fillna(0.0) > config.max_stale_minutes, "stale_quote")
    reason = reason.mask(out["any_crossed"].fillna(False).astype(bool), "crossed_book")
    reason = reason.mask(
        exhaustive.isin([Exhaustive.GAP, Exhaustive.OPEN_TAIL, Exhaustive.UNPROVEN]),
        "not_exhaustive")
    reason = reason.mask(shapes.isin([Shape.LADDER, Shape.MIXED, Shape.SINGLETON]), "wrong_shape")

    out["reason"] = reason
    return out


def funnel(triaged: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Counts and shares by cause, optionally split by another column."""
    if not len(triaged):
        return pd.DataFrame(columns=["reason", "flags", "share"])

    keys = ([by] if by else []) + ["reason"]
    counts = triaged.groupby(keys, dropna=False).size().rename("flags").reset_index()
    denominator = counts.groupby(by)["flags"].transform("sum") if by else counts["flags"].sum()
    counts["share"] = counts["flags"] / denominator
    order = {reason: i for i, reason in enumerate(REASONS)}
    counts["_order"] = counts["reason"].map(order).fillna(len(REASONS))
    sort_keys = ([by] if by else []) + ["_order"]
    return counts.sort_values(sort_keys).drop(columns="_order").reset_index(drop=True)


def band_profile(screened: pd.DataFrame) -> pd.DataFrame:
    """How wide the no-arbitrage band is, by event and by leg count.

    The half-width is the sum of the per-leg half-spreads, so it is mechanically
    increasing in leg count for a fixed quoting standard. Reporting the median
    per-leg half-spread alongside it separates the two: an event with a wide
    band because it has thirty legs is not the same as an event with a wide band
    because every leg is quoted five cents wide.
    """
    if not len(screened):
        return pd.DataFrame()
    frame = screened.copy()
    frame["half_spread_per_leg"] = frame["band"] / frame["n_legs"]
    grouped = frame.groupby("event_ticker", sort=False).agg(
        n_legs=("n_legs", "max"),
        minutes=("ts", "nunique"),
        median_band=("band", "median"),
        median_half_spread_per_leg=("half_spread_per_leg", "median"),
        median_mid_sum=("mid_sum", "median"),
        median_gini=("gini_simpson", "median"),
    ).reset_index()
    # How far the mid sum typically sits from par, in units of the band. Under
    # one means the event never leaves its own no-arbitrage interval.
    grouped["typical_distance_in_bands"] = (
        (grouped["median_mid_sum"] - 100.0).abs() / grouped["median_band"])
    return grouped.sort_values("n_legs").reset_index(drop=True)


def survivors(triaged: pd.DataFrame, min_net: float = 0.0) -> pd.DataFrame:
    """The flags that clear every test, sorted by how much they clear it by."""
    if not len(triaged):
        return triaged
    kept = triaged[(triaged["reason"] == "survives") & (triaged["best_net"] > min_net)]
    columns = [c for c in ("event_ticker", "ts", "side", "n_legs", "gross", "band",
                           "band_ratio", "best_net", "best_size", "min_offer_size",
                           "max_stale", "gini_simpson") if c in kept.columns]
    return kept[columns].sort_values("best_net", ascending=False).reset_index(drop=True)


def stale_leg_detail(panel: pd.DataFrame, flags: pd.DataFrame,
                     max_stale_minutes: float = 5.0, limit: int = 20) -> pd.DataFrame:
    """The individual legs responsible for staleness-attributed flags.

    Useful as a sanity check on the whole exercise: if the stale legs turn out to
    be spread evenly over the bracket, the staleness story is weak, whereas in
    practice they cluster on the cheap tails, which is exactly where a market
    maker has least reason to re-quote.
    """
    if not len(flags):
        return pd.DataFrame()
    keys = flags[["event_ticker", "ts"]].drop_duplicates()
    merged = panel.merge(keys, on=["event_ticker", "ts"], how="inner")
    stale = merged[merged["stale_minutes"] > max_stale_minutes]
    columns = ["event_ticker", "ts", "ticker", "yes_bid", "no_bid", "yes_ask_derived",
               "yes_ask_size", "stale_minutes"]
    return (stale[columns].sort_values("stale_minutes", ascending=False)
            .head(limit).reset_index(drop=True))
