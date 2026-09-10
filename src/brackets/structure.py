"""What kind of multi-outcome object is this event, and is it actually exhaustive?

Kalshi groups markets into events, and it is tempting to treat every event with
several markets in it as a probability distribution whose legs ought to sum to a
dollar. That is wrong often enough to matter, and it is wrong in two different
ways.

The first is the difference between a **partition** and a **threshold ladder**.
A partition splits the settlement variable into disjoint buckets that between
them cover every outcome: the highest temperature in New York is either 84 or
below, or 85-86, or 87-88, and so on. Exactly one leg pays, so the implied
probabilities are constrained to sum to one, and buying every leg for less than
a dollar is risk free. A ladder asks the same question at a sequence of
thresholds: is Bitcoin above 70,100, above 70,200, above 70,300. Those legs are
nested, not disjoint. Nothing constrains them to sum to anything; the only
constraint is that the sequence is monotone. Summing a ladder produces a number
that looks like a colossal overround and means nothing at all. Both shapes live
under one ``event_ticker`` and the API gives no field that separates them, so
the separation has to be made from the strikes.

The second is **exhaustiveness**, and this is the one that decides whether the
strategy is arbitrage or a bet. Sharing an event ticker proves nothing: an event
can hold five candidates out of a field of nine, and buying all five below par
is simply a wager that the winner is in the group. What proves exhaustiveness is
the settlement terms -- the floor and cap strikes and the rules text that
defines them -- covering the whole support of the settlement variable with no
gap and with both tails open. That test is not free, because coverage depends on
what values the settlement variable can take. New York temperature buckets run
84-or-below, 85-86, 87-88: as intervals on the real line they leave 86.5
uncovered, and they only tile because the settlement source reports whole
degrees. A support model has to come out of the rules before coverage can be
checked at all.

Kalshi does publish ``mutually_exclusive`` on the event and a
``collateral_return_type`` of ``MECNET`` when it nets collateral across legs.
Both are read here, but only as corroboration. Exclusivity is not
exhaustiveness, and a venue flag is a statement about margining rather than
about settlement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

INF = math.inf

# Phrases that mark a leg as the catch-all of a categorical event. Without one of
# these -- or an explicit statement in the rules that the listed outcomes are the
# only ones -- a categorical event cannot be shown to be exhaustive.
RESIDUAL_PATTERNS = (
    r"\bany other\b", r"\bsomeone else\b", r"\banother\b", r"\bnone of the above\b",
    r"\bfield\b", r"\bother\b", r"\bno winner\b", r"\bnot listed\b",
)
_RESIDUAL_RE = re.compile("|".join(RESIDUAL_PATTERNS), re.IGNORECASE)

# Settlement variables reported on a whole-number grid. Temperature is the common
# case on Kalshi and is the reason the weather buckets tile at all.
_INTEGER_HINTS = re.compile(
    r"°|\bdegrees?\b|\btemperature\b|\bnumber of\b|\bhow many\b|\bcount\b|\bseats\b",
    re.IGNORECASE,
)
_CONTINUOUS_HINTS = re.compile(
    r"\bindex\b|\bprice\b|\brate\b|\bpercent|\bUSD\b|\$", re.IGNORECASE)

# Counts cannot go below zero, so a bracket whose lowest leg is "0" covers the
# bottom of the support without an open tail. Anything else has to earn its
# tails the hard way.
_COUNT_HINTS = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b|\bseats\b|\bcuts\b",
                          re.IGNORECASE)
_RE_OUT_OF = re.compile(r"\bout of\s+([\d,]+)\b", re.IGNORECASE)

# Rules-text forms for the three strike shapes Kalshi writes.
_RE_ABOVE = re.compile(r"\bis (?:strictly )?above\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_RE_BELOW = re.compile(r"\bis less than\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_RE_BETWEEN = re.compile(
    r"\bis between\s+\$?([\d,]+(?:\.\d+)?)\s+and\s+\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE)


class Shape(str, Enum):
    """The structural family an event belongs to."""

    PARTITION = "partition"
    LADDER = "ladder"
    CATEGORICAL = "categorical"
    SINGLETON = "singleton"
    MIXED = "mixed"


class Exhaustive(str, Enum):
    """How far the settlement terms go towards proving the legs cover everything."""

    PROVEN = "proven"                    # strikes tile the support, both tails open
    RESIDUAL_LEG = "residual_leg"        # a catch-all leg is listed
    GAP = "gap"                          # a hole between adjacent legs
    OPEN_TAIL = "open_tail"              # one or both tails unlisted
    OVERLAP = "overlap"                  # legs overlap, so not even exclusive
    UNPROVEN = "unproven"                # no numeric strikes and no residual leg
    NOT_APPLICABLE = "not_applicable"    # ladders and singletons


@dataclass(frozen=True)
class SupportModel:
    """What values the settlement variable can take, and where we learned that.

    ``step`` is the resolution of the settlement source: the smallest gap
    between two outcomes it can distinguish. It decides whether adjacent
    brackets meet. Kalshi writes strikes at exactly that resolution, which is
    what the ``.99`` convention is for -- "above 4,199.99" and "4,200 to 4,299.99"
    leave nothing between them precisely because the index is published to the
    cent. Weather brackets do the same thing at a coarser grid, jumping 85-86
    then 87-88 because the observation is a whole number of degrees.

    ``lo_bound`` and ``hi_bound`` are the ends of the support where the variable
    has them. A count cannot be negative, so a bracket whose lowest leg is "0"
    covers the bottom of its range without needing an open tail. Leaving these
    unset is the conservative choice: an unbounded end demands an open leg.
    """

    kind: str
    step: float
    source: str
    lo_bound: float | None = None
    hi_bound: float | None = None

    @property
    def is_integer(self) -> bool:
        return self.kind == "integer"


@dataclass(frozen=True)
class Leg:
    """One market, reduced to the interval of the settlement variable it pays on."""

    ticker: str
    lo: float
    hi: float
    label: str = ""
    rules_agree: bool | None = None

    @property
    def one_sided(self) -> bool:
        return math.isinf(self.lo) != math.isinf(self.hi)

    @property
    def numeric(self) -> bool:
        return not (math.isinf(self.lo) and math.isinf(self.hi))


@dataclass
class EventStructure:
    """The verdict on one event."""

    event_ticker: str
    shape: Shape
    exhaustive: Exhaustive
    legs: list[Leg]
    support: SupportModel | None = None
    evidence: list[str] = field(default_factory=list)
    venue_mutually_exclusive: bool | None = None
    venue_collateral_type: str | None = None

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    @property
    def screenable(self) -> bool:
        """Whether summing the legs against par is a meaningful thing to do.

        A numeric bracket earns this outright: disjoint intervals that tile the
        support are exclusive and exhaustive by construction, both facts read
        off the settlement terms.

        A categorical event is different. A catch-all leg settles
        exhaustiveness, but nothing in the rules text rules out two named
        outcomes both happening, and leg labels cannot be reasoned about the way
        intervals can. So exclusivity there is taken from the venue's own
        ``mutually_exclusive`` flag -- which is the one question that flag really
        does answer -- and an event without it is left out rather than assumed.
        """
        if self.shape is Shape.PARTITION:
            return self.exhaustive in (Exhaustive.PROVEN, Exhaustive.RESIDUAL_LEG)
        if self.shape is Shape.CATEGORICAL and self.exhaustive is Exhaustive.RESIDUAL_LEG:
            return bool(self.venue_mutually_exclusive)
        return False

    @property
    def venue_agrees(self) -> bool | None:
        """Does the venue's own exclusivity flag line up with the strike geometry?

        A disagreement is worth a look in either direction. An event flagged
        mutually exclusive whose strikes overlap is a listing error; a partition
        the venue does not net collateral on is a partition you will be posting
        full margin against, which changes the economics even when the arithmetic
        works.
        """
        if self.venue_mutually_exclusive is None:
            return None
        ours = self.shape is Shape.PARTITION
        return ours == bool(self.venue_mutually_exclusive)


def _decimal_places(value: float) -> int:
    """How finely a strike is written, which is how finely the source reports."""
    text = f"{value!r}"
    if "e" in text or "E" in text:
        return 0
    _, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    return len(frac)


def infer_support(markets: Sequence[dict]) -> SupportModel:
    """Decide what grid the settlement variable lives on, and where it ends.

    Rules text comes first because it is the contract. Where it is silent about
    resolution, the strikes themselves carry the answer: Kalshi writes them at
    the precision of the settlement source, so the number of decimal places on
    the strike grid *is* the tick. Reading it off the strikes rather than
    guessing is what lets "4,199.99" and "4,200" be recognised as touching
    instead of being reported as a hole in the bracket.
    """
    text = " ".join(
        str(m.get(key) or "")
        for m in markets
        for key in ("rules_primary", "yes_sub_title", "title")
    )
    strikes = sorted({float(s) for m in markets
                      for s in (m.get("floor_strike"), m.get("cap_strike")) if s is not None})

    lo_bound = 0.0 if _COUNT_HINTS.search(text) else None
    hi_bound = None
    out_of = _RE_OUT_OF.search(text)
    if out_of and lo_bound is not None:
        hi_bound = float(out_of.group(1).replace(",", ""))

    if _INTEGER_HINTS.search(text) and not re.search(r"\$", text):
        return SupportModel("integer", 1.0, "rules describe a whole-number quantity",
                            lo_bound, hi_bound)

    if strikes:
        places = max(_decimal_places(s) for s in strikes)
        step = 10.0 ** (-places)
        if places == 0:
            return SupportModel("integer", 1.0, "every strike is written as a whole number",
                                lo_bound, hi_bound)
        return SupportModel("continuous", step,
                            f"strikes are written to {places} decimal places, "
                            f"so the source resolves to {step:g}",
                            lo_bound, hi_bound)

    if _CONTINUOUS_HINTS.search(text):
        return SupportModel("continuous", 0.0, "rules settle against a price or index",
                            lo_bound, hi_bound)
    return SupportModel("continuous", 0.0, "no evidence of a grid; assumed continuous",
                        lo_bound, hi_bound)


def leg_interval(market: dict) -> tuple[float, float]:
    """The interval of the settlement variable on which this market pays YES.

    Kalshi's convention, readable straight off the subtitles: a market with both
    a floor and a cap pays on the closed range between them ("85 to 86"), while a
    market with only one of the two is a strict threshold ("above 70,100",
    "84 or below", the latter carrying ``cap_strike = 85``).
    """
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    if floor is not None and cap is not None:
        return float(floor), float(cap)
    if floor is not None:
        return float(floor), INF
    if cap is not None:
        return -INF, float(cap)
    return -INF, INF


def _rules_agree(market: dict, lo: float, hi: float) -> bool | None:
    """Cross-check the strike fields against the sentence that defines settlement.

    The strike fields are metadata and the rules text is the contract. When they
    disagree the event is mis-assembled and no arithmetic built on the strikes
    can be trusted, so this is checked before anything else uses the interval.
    """
    text = str(market.get("rules_primary") or "")
    if not text:
        return None

    def num(raw: str) -> float:
        return float(raw.replace(",", ""))

    match = _RE_BETWEEN.search(text)
    if match:
        return math.isclose(lo, num(match.group(1)), abs_tol=1.0) and \
               math.isclose(hi, num(match.group(2)), abs_tol=1.0)
    match = _RE_ABOVE.search(text)
    if match:
        return math.isinf(hi) and math.isclose(lo, num(match.group(1)), abs_tol=1e-6)
    match = _RE_BELOW.search(text)
    if match:
        return math.isinf(lo) and math.isclose(hi, num(match.group(1)), abs_tol=1e-6)
    return None


def _adjacent(prev_hi: float, next_lo: float, support: SupportModel) -> bool:
    """Do two consecutive legs meet with no outcome falling between them?

    On a continuous variable the only way to leave nothing out is to butt the
    endpoints together. On an integer grid a gap of one step is fine, because
    ``[85, 86]`` followed by ``[87, 88]`` leaves no *integer* uncovered.
    """
    return (next_lo - prev_hi) <= support.step + 1e-9


def classify(event: dict, markets: Sequence[dict] | None = None,
             domain_bounds: dict[str, tuple[float | None, float | None]] | None = None
             ) -> EventStructure:
    """Work out the shape of an event and how far its legs are proven to cover.

    Only tradeable legs are considered. A settled or closed leg no longer covers
    anything, so an event that was a partition yesterday is merely a subset of
    one once part of it stops quoting.

    ``domain_bounds`` maps a series ticker to bounds on its settlement variable
    that a reader knows but the rules text never states -- the Senate has a
    hundred seats whether or not the market says so. Supplying one is an
    assumption, so it is recorded in the evidence rather than folded in
    silently.
    """
    markets = list(markets if markets is not None else event.get("markets") or [])
    live = [m for m in markets
            if str(m.get("status", "active")).lower() in ("active", "open", "initialized")]
    ticker = str(event.get("event_ticker") or event.get("ticker") or "?")
    venue_me = event.get("mutually_exclusive")
    venue_ct = event.get("collateral_return_type") or None

    def build(shape: Shape, exhaustive: Exhaustive, legs: list[Leg],
              support: SupportModel | None, evidence: list[str]) -> EventStructure:
        return EventStructure(
            event_ticker=ticker, shape=shape, exhaustive=exhaustive, legs=legs,
            support=support, evidence=evidence,
            venue_mutually_exclusive=venue_me, venue_collateral_type=venue_ct,
        )

    if len(live) <= 1:
        legs = [Leg(str(m.get("ticker")), *leg_interval(m),
                    label=str(m.get("yes_sub_title") or "")) for m in live]
        return build(Shape.SINGLETON, Exhaustive.NOT_APPLICABLE, legs, None,
                     ["fewer than two tradeable legs"])

    support = infer_support(live)
    override = (domain_bounds or {}).get(str(event.get("series_ticker") or ""))
    if override:
        lo, hi = override
        support = SupportModel(
            support.kind, support.step,
            support.source + "; bounds supplied by configuration, not by the rules",
            lo if lo is not None else support.lo_bound,
            hi if hi is not None else support.hi_bound,
        )

    legs: list[Leg] = []
    for m in live:
        lo, hi = leg_interval(m)
        legs.append(Leg(
            ticker=str(m.get("ticker")),
            lo=lo, hi=hi,
            label=str(m.get("yes_sub_title") or ""),
            rules_agree=_rules_agree(m, lo, hi),
        ))

    evidence: list[str] = [f"support: {support.kind} ({support.source})"]

    disagreeing = [leg.ticker for leg in legs if leg.rules_agree is False]
    if disagreeing:
        evidence.append(
            f"{len(disagreeing)} leg(s) whose strike fields contradict their rules text, "
            f"first is {disagreeing[0]}")

    numeric = [leg for leg in legs if leg.numeric]
    if len(numeric) < len(legs) or not numeric:
        residual = [leg for leg in legs if _RESIDUAL_RE.search(leg.label)]
        if residual:
            evidence.append(f"catch-all leg listed: {residual[0].label!r}")
            return build(Shape.CATEGORICAL, Exhaustive.RESIDUAL_LEG, legs, support, evidence)
        evidence.append("no numeric strikes and no catch-all leg, so coverage is unproven")
        return build(Shape.CATEGORICAL, Exhaustive.UNPROVEN, legs, support, evidence)

    one_sided = [leg for leg in legs if leg.one_sided]
    lower_open = [leg for leg in legs if math.isinf(leg.lo)]
    upper_open = [leg for leg in legs if math.isinf(leg.hi)]

    # A ladder is every leg open in the same direction: nested thresholds, so
    # exactly one open tail on each leg and no closed buckets at all.
    if len(one_sided) == len(legs) and (len(upper_open) == len(legs) or len(lower_open) == len(legs)):
        direction = "upward" if len(upper_open) == len(legs) else "downward"
        evidence.append(f"every leg is a one-sided {direction} threshold, so the legs nest")
        return build(Shape.LADDER, Exhaustive.NOT_APPLICABLE, legs, support, evidence)

    # Anything else numeric is a candidate partition: check the intervals tile.
    ordered = sorted(legs, key=lambda leg: (leg.lo, leg.hi))
    if len(lower_open) > 1 or len(upper_open) > 1:
        evidence.append(
            f"{len(lower_open)} leg(s) open below and {len(upper_open)} open above, so some "
            "legs contain others")
        return build(Shape.MIXED, Exhaustive.OVERLAP, ordered, support, evidence)

    # The ends of the bracket only need an open leg where the settlement
    # variable is actually unbounded. A count that starts at zero is covered by
    # a leg that starts at zero.
    lowest, highest = ordered[0], ordered[-1]
    covers_low = math.isinf(lowest.lo) or (
        support.lo_bound is not None and lowest.lo <= support.lo_bound + 1e-9)
    covers_high = math.isinf(highest.hi) or (
        support.hi_bound is not None and highest.hi >= support.hi_bound - 1e-9)
    if not (covers_low and covers_high):
        missing = []
        if not covers_low:
            missing.append(f"below {lowest.lo:g}")
        if not covers_high:
            missing.append(f"above {highest.hi:g}")
        evidence.append(
            "outcomes " + " and ".join(missing) + " are not listed, and nothing in the "
            "settlement terms bounds the variable there")
        return build(Shape.PARTITION, Exhaustive.OPEN_TAIL, ordered, support, evidence)

    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.lo < prev.hi - 1e-9:
            evidence.append(
                f"{prev.ticker} and {nxt.ticker} overlap, so the legs are not exclusive")
            return build(Shape.MIXED, Exhaustive.OVERLAP, ordered, support, evidence)
        if not _adjacent(prev.hi, nxt.lo, support):
            evidence.append(
                f"gap between {prev.ticker} (ends {prev.hi:g}) and {nxt.ticker} "
                f"(starts {nxt.lo:g}) that the support model does not close")
            return build(Shape.PARTITION, Exhaustive.GAP, ordered, support, evidence)

    evidence.append(
        f"{len(ordered)} legs tile the support with both tails open, so exactly one settles YES")
    return build(Shape.PARTITION, Exhaustive.PROVEN, ordered, support, evidence)


def ladder_to_buckets(structure: EventStructure) -> list[tuple[str, str]]:
    """Adjacent rung pairs whose difference is an implied bucket probability.

    A ladder carries the same information as a partition, but it is not the same
    tradeable object. ``P(a < X <= b) = P(X > a) - P(X > b)``, and to hold that
    difference you buy the lower rung and sell the higher one, crossing the
    spread in opposite directions on the two legs. The implied bucket therefore
    comes with a range rather than a price, and the two open tails of the ladder
    are not tradeable at all, so a ladder can never be assembled into a complete
    bracket the way a listed partition can.
    """
    if structure.shape is not Shape.LADDER:
        return []
    upward = all(math.isinf(leg.hi) for leg in structure.legs)
    rungs = sorted(structure.legs, key=lambda leg: leg.lo if upward else leg.hi)
    return [(a.ticker, b.ticker) for a, b in zip(rungs, rungs[1:])]
