"""A synthetic quote panel with known answers.

Testing a screen against live data only ever shows that it produced a number.
This module builds a panel where the answer is known in advance: a partition
quoted around a moving fair value, with specific minutes deliberately spoiled in
specific ways. A leg is frozen for a stretch to plant a staleness artefact, an
offer is thinned to plant a depth artefact, and one minute is given a genuine
dislocation big enough to clear both the band and the fee. A correct screen
finds exactly those and nothing else.

It is also what makes the repository runnable without waiting on a collection
run, since the whole pipeline downstream of :mod:`brackets.collect` takes a
panel and does not care where it came from.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# A weather-style bracket: six whole-degree buckets with open tails.
DEFAULT_LEGS = [
    ("SIM-T85", None, 85.0, "84 or below"),
    ("SIM-B86", 85.0, 86.0, "85 to 86"),
    ("SIM-B88", 87.0, 88.0, "87 to 88"),
    ("SIM-B90", 89.0, 90.0, "89 to 90"),
    ("SIM-B92", 91.0, 92.0, "91 to 92"),
    ("SIM-T92", 92.0, None, "93 or above"),
]


@dataclass
class Planted:
    """What was deliberately broken, so a test can assert it was found."""

    stale_minutes: list[pd.Timestamp]
    thin_minutes: list[pd.Timestamp]
    arbitrage_minutes: list[pd.Timestamp]


def simulate_panel(minutes: int = 120, seed: int = 7, half_spread: float = 0.5,
                   stale_from: int = 30, stale_for: int = 20,
                   thin_at: int = 70, arbitrage_at: int = 95,
                   arbitrage_cents: float = 4.0,
                   event_ticker: str = "SIM-EVENT") -> tuple[pd.DataFrame, Planted]:
    """Build a minute panel around a random-walking fair distribution.

    The fair probabilities are a softmax over a slowly drifting location, which
    keeps them summing to one and keeps the dispersion realistic: most of the
    mass sits on one or two buckets and the tails stay near a cent, which is the
    shape that makes the fee floor bite.
    """
    rng = np.random.default_rng(seed)
    centres = np.array([84.0, 85.5, 87.5, 89.5, 91.5, 93.0])
    n_legs = len(DEFAULT_LEGS)
    stamps = pd.date_range("2026-09-10 12:00", periods=minutes, freq="min", tz="UTC")
    location = 88.0 + np.cumsum(rng.normal(0.0, 0.12, minutes))

    weights = np.exp(-0.5 * ((centres[None, :] - location[:, None]) / 1.6) ** 2)
    mids = np.round(100.0 * weights / weights.sum(axis=1, keepdims=True))
    # Rounding has to go somewhere; put it on the heaviest leg so the mids still
    # sum to par exactly, as a real partition's fair value would.
    heaviest = mids.argmax(axis=1)
    mids[np.arange(minutes), heaviest] += 100.0 - mids.sum(axis=1)

    # A genuine dislocation: push the mid sum below par by more than the
    # accumulated half-spreads and the fee together.
    if 0 <= arbitrage_at < minutes:
        mids[arbitrage_at, heaviest[arbitrage_at]] -= (
            arbitrage_cents + 2 * half_spread * n_legs)

    yes_bid = np.clip(mids - half_spread, 0.0, 99.0)
    no_bid = np.clip(100.0 - mids - half_spread, 0.0, 99.0)
    sizes = np.full((minutes, n_legs), 400.0)

    # Freeze one leg for a stretch: it keeps displaying the quote it held when
    # the stretch began while the rest of the bracket moves on without it.
    stale_leg, thin_leg = 2, 4
    stale_end = min(stale_from + stale_for, minutes)
    if 0 <= stale_from < stale_end:
        yes_bid[stale_from:stale_end, stale_leg] = yes_bid[stale_from, stale_leg]
        no_bid[stale_from:stale_end, stale_leg] = no_bid[stale_from, stale_leg]
    if 0 <= thin_at < minutes:
        sizes[thin_at, thin_leg] = 2.0

    rows = []
    for i, stamp in enumerate(stamps):
        for j, (ticker, _lo, _hi, _label) in enumerate(DEFAULT_LEGS):
            rows.append({
                "ts": stamp, "event_ticker": event_ticker, "series_ticker": "SIM",
                "ticker": ticker,
                "yes_bid": float(yes_bid[i, j]), "no_bid": float(no_bid[i, j]),
                "yes_ask": float(100.0 - no_bid[i, j]),
                "no_ask": float(100.0 - yes_bid[i, j]),
                "yes_bid_size": 400.0, "yes_ask_size": float(sizes[i, j]),
                "last_price": float(mids[i, j]), "volume": 1000.0, "volume_24h": 1000.0,
                "open_interest": 5000.0, "liquidity": 0.0, "status": "active",
            })

    # Only the part of the frozen stretch past the usual five-minute tolerance
    # counts as planted staleness; before that the quote is merely quiet.
    stale_window = stamps[min(stale_from + 6, minutes):stale_end]
    planted = Planted(
        stale_minutes=list(stale_window),
        thin_minutes=[stamps[thin_at]] if 0 <= thin_at < minutes else [],
        arbitrage_minutes=[stamps[arbitrage_at]] if 0 <= arbitrage_at < minutes else [],
    )
    return pd.DataFrame(rows), planted


def simulated_universe(event_ticker: str = "SIM-EVENT") -> list[dict]:
    """The universe rows matching :func:`simulate_panel`, for the screen to use."""
    return [{
        "event_ticker": event_ticker,
        "markets": [{"ticker": t, "floor_strike": lo, "cap_strike": hi,
                     "yes_sub_title": label, "status": "active",
                     "rules_primary": "the maximum temperature in degrees"}
                    for t, lo, hi, label in DEFAULT_LEGS],
        "mutually_exclusive": True,
        "collateral_return_type": "MECNET",
        "title": "Simulated highest temperature bracket",
    }]
