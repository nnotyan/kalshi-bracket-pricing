"""Kalshi's fee function, and what it does to the cost of assembling a bracket.

Kalshi charges the taker a fee that is quadratic in price::

    fee = roundup_to_cent( rate * C * P * (1 - P) )

with ``C`` the number of contracts, ``P`` the execution price in dollars and
``rate`` the published trading-fee rate (7% on the general schedule). The
round-up is applied *per order*, not per contract and not per fill.

Two consequences matter for this project.

First, the continuous term collapses to a diversity index. Ignore the rounding
for a moment and add the fee up over the legs of an exhaustive partition
executed at implied probabilities ``p_i``::

    total / C = rate * sum_i p_i (1 - p_i)
              = rate * ( sum_i p_i  -  sum_i p_i^2 )

On a partition the implied probabilities sum to one, so this is exactly::

    total / C = rate * ( 1 - sum_i p_i^2 ) = rate * D

where ``D`` is the Gini-Simpson index of the implied distribution. The cost of
buying the whole bracket does not depend on how much edge is on the table, or on
how many legs there are as such: it depends on how *dispersed* the implied
distribution is. A twelve-leg partition with one near-certain outcome is cheap
to assemble; a three-leg coin flip is expensive.

Second, the rounding puts a floor under every leg. Each leg is a separate order,
so each leg pays at least one cent however small the position. A partition with
many near-zero legs pays ``n`` cents on a one-contract order even though its
continuous cost is a fraction of a cent. Because the floor scales with leg count
and the continuous term scales with dispersion, the ranking of two partition
structures by assembly cost can and does reverse with order size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# Kalshi's general trading fee schedule. A handful of series run at a different
# rate and some carry a separate maker fee, so this is only a default.
DEFAULT_FEE_RATE = 0.07

# Kalshi's published maker fee on the series that charge one, in cents per
# contract. Assembling a bracket by lifting resting offers is a taker action on
# every leg, so this only matters if legs are worked passively.
MAKER_FEE_CENTS = 0.25


def _as_dollars(prices: Iterable[float], unit: str) -> np.ndarray:
    arr = np.asarray(list(prices), dtype="float64")
    if unit == "cents":
        return arr / 100.0
    if unit == "dollars":
        return arr
    raise ValueError("unit must be 'cents' or 'dollars', got " + repr(unit))


def fee_cents(contracts: int, price: float, rate: float = DEFAULT_FEE_RATE,
              unit: str = "cents") -> int:
    """Fee in whole cents for a single order of ``contracts`` at one price."""
    if contracts <= 0:
        return 0
    p = _as_dollars([price], unit)[0]
    if not 0.0 < p < 1.0:
        return 0
    raw_cents = rate * 100.0 * contracts * p * (1.0 - p)
    # Guard the round-up against binary representation error: 1.0000000001 cents
    # of raw fee should cost one cent, not two.
    return int(math.ceil(round(raw_cents, 9)))


def bracket_fee_cents(contracts: int, prices: Sequence[float],
                      rate: float = DEFAULT_FEE_RATE, unit: str = "cents",
                      orders_per_leg: int = 1) -> int:
    """Total fee to buy ``contracts`` of every leg, with the per-order round-up.

    ``orders_per_leg`` above one models working a leg in clips: the round-up
    applies to each order, so slicing a leg never costs less than sending it in
    one go.
    """
    if orders_per_leg < 1:
        raise ValueError("orders_per_leg must be at least 1")
    base, remainder = divmod(contracts, orders_per_leg)
    total = 0
    for price in prices:
        for i in range(orders_per_leg):
            size = base + (1 if i < remainder else 0)
            total += fee_cents(size, price, rate=rate, unit=unit)
    return total


def continuous_fee_cents_per_bracket(prices: Sequence[float],
                                     rate: float = DEFAULT_FEE_RATE,
                                     unit: str = "cents") -> float:
    """The fee per bracket in the large-order limit, before any rounding."""
    p = _as_dollars(prices, unit)
    return float(rate * 100.0 * np.sum(p * (1.0 - p)))


def gini_simpson(prices: Sequence[float], unit: str = "cents",
                 normalise: bool = True) -> float:
    """Gini-Simpson index ``1 - sum q_i^2`` of the implied distribution.

    The implied probabilities of a real bracket sum to slightly more or less
    than one, because they are execution prices rather than beliefs. With
    ``normalise`` the vector is rescaled to sum to one first, which is what makes
    the result a genuine diversity index bounded above by ``1 - 1/n``.
    """
    p = _as_dollars(prices, unit)
    total = float(np.sum(p))
    if total <= 0:
        return 0.0
    q = p / total if normalise else p
    return float(1.0 - np.sum(q * q))


def max_gini_simpson(n_legs: int) -> float:
    """The index is maximised by a uniform partition, at ``1 - 1/n``."""
    return 1.0 - 1.0 / n_legs if n_legs > 0 else 0.0


def fee_ceiling_cents(n_legs: int, rate: float = DEFAULT_FEE_RATE) -> float:
    """Worst-case continuous assembly cost for an ``n``-leg partition, in cents.

    Reached by a uniform partition. It rises with leg count but is bounded by
    ``rate * 100`` cents no matter how many legs there are, which is seven cents
    on the general schedule. That is a useful sanity bound: a screen reporting a
    gross edge larger than the fee ceiling is reporting something structural
    rather than a fee-sensitive marginal call.
    """
    return rate * 100.0 * max_gini_simpson(n_legs)


def floor_binding_size(price: float, rate: float = DEFAULT_FEE_RATE,
                       unit: str = "cents") -> float:
    """Order size below which a leg pays the one-cent floor rather than the rate.

    A one-cent leg has ``p(1-p)`` of about 0.01, so on the 7% schedule the floor
    binds up to roughly fourteen contracts. Under that size the leg is charged
    more than its continuous rate, and at a single contract about fourteen
    times as much.
    """
    p = _as_dollars([price], unit)[0]
    if not 0.0 < p < 1.0:
        return math.inf
    return 1.0 / (rate * 100.0 * p * (1.0 - p))


@dataclass(frozen=True)
class AssemblyCost:
    """Cost of buying one full bracket at a given clip size, in cents."""

    contracts: int
    n_legs: int
    total_fee_cents: int
    per_bracket_cents: float
    continuous_cents: float
    gini_simpson: float
    floor_legs: int

    @property
    def floor_premium_cents(self) -> float:
        """How much the per-order round-up costs above the continuous rate."""
        return self.per_bracket_cents - self.continuous_cents


def assembly_cost(prices: Sequence[float], contracts: int,
                  rate: float = DEFAULT_FEE_RATE, unit: str = "cents") -> AssemblyCost:
    """Full cost breakdown for assembling one bracket at a given order size."""
    prices = list(prices)
    total = bracket_fee_cents(contracts, prices, rate=rate, unit=unit)
    continuous = continuous_fee_cents_per_bracket(prices, rate=rate, unit=unit)
    p = _as_dollars(prices, unit)
    floor_legs = int(sum(1 for x in p if 0.0 < x < 1.0
                         and rate * 100.0 * contracts * x * (1.0 - x) < 1.0))
    return AssemblyCost(
        contracts=contracts,
        n_legs=len(prices),
        total_fee_cents=total,
        per_bracket_cents=total / contracts if contracts else math.inf,
        continuous_cents=continuous,
        gini_simpson=gini_simpson(prices, unit=unit),
        floor_legs=floor_legs,
    )


def cost_curve(prices: Sequence[float], sizes: Sequence[int],
               rate: float = DEFAULT_FEE_RATE, unit: str = "cents") -> np.ndarray:
    """Per-bracket assembly cost in cents across a range of order sizes."""
    return np.array([bracket_fee_cents(c, prices, rate=rate, unit=unit) / c
                     for c in sizes], dtype="float64")


def crossover_size(cheap_at_scale: Sequence[float], cheap_when_small: Sequence[float],
                   rate: float = DEFAULT_FEE_RATE, unit: str = "cents",
                   max_contracts: int = 5000) -> int | None:
    """Smallest order size at which the first structure becomes the cheaper one.

    Returns ``None`` if the first structure never overtakes the second within
    ``max_contracts``, which is the answer whenever the two do not actually
    trade leg count off against dispersion.
    """
    for contracts in range(1, max_contracts + 1):
        a = bracket_fee_cents(contracts, cheap_at_scale, rate=rate, unit=unit) / contracts
        b = bracket_fee_cents(contracts, cheap_when_small, rate=rate, unit=unit) / contracts
        if a < b:
            return contracts
    return None
