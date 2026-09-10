# Deciding what an event actually is

An event ticker on Kalshi is a grouping, not a claim. `classify()` in
[`structure.py`](../src/brackets/structure.py) reads the settlement terms of the
legs and returns two verdicts: what **shape** the event has, and how far its
legs are **proven to cover** the outcome space. Only the combination decides
whether summing the legs against a dollar means anything.

## Shape

Each leg is reduced to the interval of the settlement variable on which it pays
YES, read off `floor_strike` and `cap_strike` under Kalshi's convention:

| strikes present | YES region | example subtitle |
|---|---|---|
| floor and cap | `[floor, cap]` | "85° to 86°" |
| floor only | `(floor, inf)` | "$70,100 or above" |
| cap only | `(-inf, cap)` | "84° or below" |
| neither | non-numeric | "Alice" |

From there the shape follows:

- **partition** — the intervals are disjoint. Exactly one leg can settle YES.
- **ladder** — every leg is one-sided in the same direction. The legs nest, so
  more than one settles YES and nothing constrains the sum.
- **categorical** — no numeric strikes; the legs are named outcomes.
- **mixed** / **singleton** — overlapping intervals, or fewer than two live legs.

![Partition against ladder](img/structure_taxonomy.png)

Ladders are not a rare corner. On the open board, they are the overwhelming
majority of multi-outcome events, because every sports prop of the form "more
than N yards" and every hourly crypto threshold is one. The eighty-rung Bitcoin
ladder in the sample panel sums to an apparent short-side edge of about thirty
dollars, seventy-one times its own band, on every minute it was observed. That
is why shape is the first thing the screen tests and the first reason the triage
assigns.

## Exhaustiveness

Disjointness is not enough. The Dutch book is only risk free if some leg is
*certain* to pay, and that is a question about the settlement terms, not about
which markets happen to share a ticker. An event listing five of nine candidates
is a perfectly ordinary event; buying all five below par is a bet that the
winner is in the group.

For numeric brackets the test is coverage: the intervals must tile the support
of the settlement variable, with no gap between adjacent legs and with both ends
of the support reached. Two things make that harder than it sounds.

### The settlement grid

New York temperature buckets run `84 or below`, `85-86`, `87-88`, `89-90`,
`91-92`, `93 or above`. As intervals on the real line those leave 86.5
uncovered. They tile only because the settlement source reports whole degrees.

The same problem shows up in reverse on index brackets, where Kalshi writes
strikes like `4,199.99` and `4,200`. Those touch — nothing lies between them —
because the S&P close is published to the cent, and the `.99` convention exists
precisely to say so.

So coverage cannot be checked without a model of what values the settlement
variable can take. `infer_support` builds one:

1. If the rules text describes a whole-number quantity (degrees, counts, seats),
   the grid is the integers.
2. Otherwise the grid is read off the strikes themselves. Kalshi writes strikes
   at the precision of the settlement source, so the number of decimal places on
   the strike grid *is* the tick — two decimals means a tick of 0.01.
3. Failing both, the variable is assumed continuous, which is the conservative
   choice: it makes any gap a real gap.

The model that came out is recorded in the evidence list on every verdict, so a
"proven" is always readable as "proven under this assumption".

It is worth knowing how much weight that assumption carries, so
`brackets board --strict-support` re-runs the census accepting only what the
rules text states. Of the 64 partitions the default classifier calls proven, 30
survive; the other 34 are proven only because the strike grid was read as
evidence of the settlement tick. That is a large enough share to be worth
stating plainly rather than burying: the inference is a good rule, it is not a
contract term, and a third of the screenable universe depends on it.

### The ends of the support

An exhaustive numeric bracket normally needs an open leg at each end. But a
count cannot be negative, so a bracket whose lowest leg is `0` covers the bottom
without one. `infer_support` sets a lower bound of zero when the rules describe a
count, and otherwise leaves the bounds unset, which forces the open leg.

This is deliberately strict, and it produces verdicts that look pedantic. A
ten-leg bracket on Democratic Senate seats whose top leg is capped at 100 is
reported as `open_tail`, because nothing in the rules text says the Senate has
a hundred seats. That is the right default: the alternative is a classifier that
quietly imports outside knowledge into a proof. Where the knowledge is genuinely
certain, `domain_bounds` in `config/universe.yaml` supplies it and the evidence
records that the bound came from configuration rather than from the contract.

### Categorical events

Labels cannot be reasoned about the way intervals can. A catch-all leg — "Any
other candidate", "None of the above" — settles exhaustiveness, and the
`RESIDUAL_PATTERNS` list is what looks for one. Exclusivity is the harder half:
nothing in the rules text rules out two named outcomes both settling YES, so for
categorical events alone the screen falls back on Kalshi's own
`mutually_exclusive` flag, which is the one question that flag genuinely
answers. An event without it stays out.

## What the venue flags are and are not

Kalshi publishes `mutually_exclusive` on the event and sets
`collateral_return_type` to `MECNET` when it nets collateral across legs. Both
are read and both are recorded, but neither is used to establish exhaustiveness,
for the simple reason that they are statements about margining rather than about
settlement. `venue_agrees` reports where the venue's flag and the strike
geometry disagree, which is worth looking at in both directions: an event
flagged exclusive whose strikes overlap is a listing error, and a genuine
partition the venue does *not* net is one you will be posting full margin
against, which changes the economics even when the arithmetic works.

## What the board looks like

`brackets board` classifies every open multi-outcome event. The shape of the
answer is the point: the great majority of events that look like distributions
are not ones you can sum.

![Structural census of the open board](img/board_census.png)

## Ladders are not useless, just not additive

A ladder carries the same information as a partition. Differencing adjacent
rungs gives implied bucket probabilities, `P(a < X <= b) = P(X > a) - P(X > b)`,
and `ladder_to_buckets` returns the pairs. But it is not the same tradeable
object: holding that difference means buying the low rung and selling the high
one, crossing the spread in opposite directions, so the implied bucket comes
with a range rather than a price. And the two open tails of a ladder are not
tradeable at all, so a ladder can never be assembled into a complete bracket the
way a listed partition can.

What a ladder *does* admit is a monotonicity screen. The implied probability must
fall as the threshold rises, so if a higher rung can be sold for more than a
lower rung costs — `bid(t_high) > ask(t_low)` — the pair is guaranteed to return
at least a dollar for less than a dollar. `screen_ladders` checks adjacent rungs
only, which is complete: a violation between two distant rungs implies one
between some adjacent pair.
