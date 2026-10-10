"""Turning Kalshi's bid-only book into the bid-ask pairs the arithmetic needs.

Kalshi does not run a two-sided book in the way an equity venue does. Every
resting order is a *bid*: either a bid for YES contracts or a bid for NO
contracts. There is no such thing as a resting offer. What makes the venue
tradeable anyway is the settlement identity

    YES + NO = $1.00

which holds by construction, since exactly one of the two pays out. A resting
bid for NO at ``q`` cents is therefore economically identical to an offer to
sell YES at ``100 - q``: the counterparty hands over ``100 - q`` cents, Kalshi
mints the pair, and the NO bidder gets their side at the price they bid.

So the tradeable YES ask is derived rather than quoted:

    yes_ask = 100 - no_bid
    no_ask  = 100 - yes_bid

Everything downstream depends on that inversion, which is why it lives in its
own module with its own tests. The quantity ``100 - yes_bid - no_bid`` is the
part of the dollar nobody is currently bidding for; it is the full bid-ask
spread of the market, and half of it is what a buyer pays away from mid.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PAR_CENTS = 100.0

# A derived ask of exactly par means the opposite side of the book is empty, so
# there is no price at which the contract can actually be bought. Treat it as
# "no offer" rather than as a 100-cent offer, otherwise every empty book looks
# like an expensive-but-real quote.
NO_OFFER = PAR_CENTS


@dataclass(frozen=True)
class Quote:
    """One market's book, expressed in cents, after inversion."""

    yes_bid: float
    no_bid: float

    @property
    def yes_ask(self) -> float:
        return PAR_CENTS - self.no_bid

    @property
    def no_ask(self) -> float:
        return PAR_CENTS - self.yes_bid

    @property
    def width(self) -> float:
        """Full bid-ask spread in cents: the slice of par with no bid against it."""
        return PAR_CENTS - self.yes_bid - self.no_bid

    @property
    def half_spread(self) -> float:
        return self.width / 2.0

    @property
    def mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def yes_offered(self) -> bool:
        return self.no_bid > 0.0

    @property
    def no_offered(self) -> bool:
        return self.yes_bid > 0.0

    @property
    def crossed(self) -> bool:
        """A crossed book: the two bids together promise more than par.

        This should never happen on a live venue (it is a free dollar for
        whoever sells the pair), so in practice it means one of the two legs is
        a stale snapshot rather than a real quote.
        """
        return self.width < 0.0


def invert(yes_bid: float | None, no_bid: float | None) -> Quote:
    """Build a two-sided quote from the two bid sides of a Kalshi book."""
    return Quote(yes_bid=float(yes_bid or 0.0), no_bid=float(no_bid or 0.0))


def add_derived_quotes(frame: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """Vectorised :func:`invert` over a quote panel.

    Adds ``yes_ask``, ``no_ask``, ``mid``, ``half_spread``, ``width`` and the
    two one-sided flags. Any venue-reported ask columns already present are left
    alone so that :func:`reconcile` can compare them afterwards.
    """
    out = frame.copy()
    yes_bid = out[f"{prefix}yes_bid"].astype("float64").fillna(0.0)
    no_bid = out[f"{prefix}no_bid"].astype("float64").fillna(0.0)

    out[f"{prefix}yes_ask_derived"] = PAR_CENTS - no_bid
    out[f"{prefix}no_ask_derived"] = PAR_CENTS - yes_bid
    out[f"{prefix}width"] = PAR_CENTS - yes_bid - no_bid
    out[f"{prefix}half_spread"] = out[f"{prefix}width"] / 2.0
    out[f"{prefix}mid"] = (yes_bid + out[f"{prefix}yes_ask_derived"]) / 2.0
    out[f"{prefix}yes_offered"] = no_bid > 0.0
    out[f"{prefix}no_offered"] = yes_bid > 0.0
    out[f"{prefix}crossed"] = out[f"{prefix}width"] < 0.0
    return out


def reconcile(frame: pd.DataFrame, tolerance: float = 1e-6) -> dict:
    """Check the derived asks against the ones the venue publishes.

    Kalshi publishes ``yes_ask``/``no_ask`` as a convenience even though no such
    order exists. If our inversion is right the two agree to the cent. Running
    this on every collected snapshot is the cheapest possible guard against a
    silent change in the venue's field semantics.
    """
    if "yes_ask" not in frame.columns:
        return {"checked": 0, "mismatches": 0, "max_abs_diff": 0.0}

    reported = frame["yes_ask"].astype("float64")
    derived = frame["yes_ask_derived"].astype("float64")
    both = reported.notna() & derived.notna()
    # Where the NO book is empty the venue reports its own placeholder rather
    # than par, so those rows carry no information about the identity.
    both &= frame["yes_offered"].fillna(False).astype(bool)

    diff = (reported[both] - derived[both]).abs()
    return {
        "checked": int(both.sum()),
        "mismatches": int((diff > tolerance).sum()),
        "max_abs_diff": float(diff.max()) if len(diff) else 0.0,
    }


def executable_ask(frame: pd.DataFrame, min_size: float = 0.0) -> pd.Series:
    """The YES ask a taker can actually lift, or NaN when nothing is offered.

    ``min_size`` screens out offers too thin to fill the intended clip. A quote
    that exists but has one contract behind it is not a price you can assemble a
    bracket at, and treating it as one is an easy way to manufacture a phantom
    arbitrage.
    """
    ask = frame["yes_ask_derived"].astype("float64")
    offered = frame["yes_offered"].fillna(False).astype(bool)
    if min_size > 0 and "yes_ask_size" in frame.columns:
        offered &= frame["yes_ask_size"].astype("float64").fillna(0.0) >= min_size
    return ask.where(offered, other=np.nan)
