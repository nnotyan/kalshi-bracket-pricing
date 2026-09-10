# The cost of assembling a bracket

Kalshi's trading fee is quadratic in price. For an order of `C` contracts at a
price of `P` dollars,

```
fee = roundup_to_cent( rate * C * P * (1 - P) )
```

with `rate = 0.07` on the general schedule. The round-up applies **per order**,
not per contract and not per fill. Both halves of that sentence matter, and they
pull in opposite directions.

## The continuous term is a diversity index

Drop the rounding for a moment. Buying `C` contracts on every leg of a partition
whose implied probabilities are `p_i` costs

```
total / C = rate * sum_i p_i (1 - p_i)
          = rate * ( sum_i p_i - sum_i p_i^2 )
```

and on a partition the implied probabilities sum to one, so

```
total / C = rate * ( 1 - sum_i p_i^2 ) = rate * D
```

where `D` is the [Gini-Simpson index](https://en.wikipedia.org/wiki/Diversity_index)
of the implied distribution — the probability that two independent draws land in
different buckets.

This is worth sitting with, because it says the cost of assembling a bracket has
nothing to do with how much edge is on the table and, in a real sense, nothing to
do with how many legs there are. It depends only on how spread out the implied
distribution is:

| bracket | legs | `D` | cost per bracket |
|---|---|---|---|
| coin flip | 2 | 0.500 | 3.50c |
| uniform over 3 | 3 | 0.667 | 4.67c |
| uniform over 12 | 12 | 0.917 | 6.42c |
| 12 legs, one at 88c | 12 | 0.209 | 1.50c |
| 28 legs, one at 95c | 28 | 0.097 | 0.68c |

A twenty-eight-leg crypto bracket with most of its mass on one bucket is four
times cheaper to buy outright than a three-way coin flip. And since `D` is
maximised by the uniform distribution at `1 - 1/n`, the cost is bounded above by
`rate` itself, seven cents, however many legs are involved. A screen reporting a
gross edge above seven cents is reporting something structural rather than a
fee-sensitive marginal call, which makes that bound a useful smell test.

There is a small correction away from par. Real brackets execute at asks, not at
beliefs, so `sum p_i = S` is a little above or below one and the exact
continuous cost is `rate * (S - sum p_i^2)`. The identity is stated at `S = 1`;
`continuous_fee_cents_per_bracket` computes the exact quantity and does not rely
on the approximation.

## The rounding puts a floor under every leg

Each leg is a separate order, so each leg is rounded up separately, so each leg
with a price strictly between zero and one pays **at least one cent** — however
small the position. An `n`-leg bracket therefore costs at least `n` cents on a
one-contract order, regardless of what the continuous term says.

The floor stops binding on a leg once the continuous term overtakes it, at

```
C > 1 / ( rate * 100 * p * (1 - p) )
```

which for a one-cent leg on the 7% schedule is about **fourteen contracts**.
Below that, a penny leg is being charged something like a hundred times its
continuous rate. Since cheap tails are exactly what a many-legged bracket is
made of, small orders are punished in proportion to leg count while large orders
are punished in proportion to dispersion.

## So the cheaper structure depends on the size

Two partitions, both real shapes on the venue:

- **A** — twelve legs, one at 88c and eleven at a cent. Low dispersion
  (`D = 0.21`), lots of legs.
- **B** — three legs at roughly a third each. High dispersion (`D = 0.67`),
  few legs.

| contracts per leg | A | B | cheaper |
|---|---|---|---|
| 1 | 12.00c | 6.00c | B |
| 3 | 4.67c | 5.00c | A |
| 15 | 2.27c | 4.80c | A |
| 1000 | 1.51c | 4.67c | A |

At one contract, A pays its twelve-cent floor and B pays six. By three contracts
the ranking has flipped, and it never flips back. `crossover_size` finds that
point for any pair.

![Assembly cost against order size](img/fee_size_crossover.png)

The figure draws the same reversal from two brackets that were live on the venue
at the same moment: a twenty-one-leg rate-cut-count bracket with almost all its
mass on one outcome, against a six-leg FOMC dissent bracket with its mass spread
across four. The many-legged one is three and a half times cheaper to assemble
at size and three times more expensive at a single contract.

The sawtooth in both curves is the round-up: as `C` grows, individual legs cross
a cent boundary at different sizes, so the per-bracket cost falls in steps rather
than smoothly. It is also why slicing a leg into clips is strictly worse than
sending it whole — each clip is an order and each order rounds up — which
`bracket_fee_cents(..., orders_per_leg=k)` will quantify for a given working
strategy.

## What this changes about the screen

The practical consequence is that "is this bracket worth buying" is not a
question with one answer. The screen prices the same event-minute at several
clip sizes and reports each, because a two-cent gross edge on a twenty-leg
bracket is money at a thousand contracts and a loss at ten.

## Caveats

- The 7% rate is the general schedule. A few series run at a different rate and
  some carry a separate maker fee of $0.0025 per contract; `fees.py` takes the
  rate as a parameter and `MAKER_FEE_CENTS` is defined but not applied, since
  assembling a bracket by lifting offers is a taker action on every leg.
- Fee schedules change. The formula here is the one in force at the time of
  writing and is worth re-checking against Kalshi's published schedule before
  any number in this repository is used for anything.
- Nothing here accounts for collateral. Buying every YES leg of a partition
  costs the ask sum up front; selling every leg means buying `n` NO contracts,
  which is a much larger outlay unless the venue nets it. Kalshi does net across
  mutually exclusive legs (`collateral_return_type: MECNET`), and the screen
  records that flag, but the capital cost of the short side is not modelled.
