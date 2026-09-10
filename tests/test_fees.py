"""The fee function, the diversity-index identity, and the size reversal."""

import numpy as np
import pytest

from brackets.fees import (assembly_cost, bracket_fee_cents, continuous_fee_cents_per_bracket,
                           cost_curve, crossover_size, fee_cents, fee_ceiling_cents,
                           floor_binding_size, gini_simpson, max_gini_simpson)


def test_matches_the_published_worked_example():
    # Kalshi's own illustration: 100 contracts at 50 cents costs
    # 0.07 * 100 * 0.5 * 0.5 = $1.75.
    assert fee_cents(100, 50) == 175


def test_single_contract_rounds_up_to_a_whole_cent():
    # 0.07 * 1 * 0.5 * 0.5 = 1.75 cents, and the venue rounds up.
    assert fee_cents(1, 50) == 2


def test_fee_is_symmetric_about_the_midpoint():
    assert fee_cents(500, 20) == fee_cents(500, 80)


def test_certain_legs_are_free():
    assert fee_cents(1000, 0) == 0
    assert fee_cents(1000, 100) == 0


def test_partition_cost_is_the_gini_simpson_index():
    # For a partition priced exactly at par, sum p(1-p) = 1 - sum p^2 exactly.
    prices = [40.0, 35.0, 15.0, 10.0]
    assert sum(prices) == 100.0
    continuous = continuous_fee_cents_per_bracket(prices, rate=0.07)
    assert continuous == pytest.approx(7.0 * gini_simpson(prices))


def test_uniform_partition_maximises_the_index():
    n = 8
    uniform = [100.0 / n] * n
    skewed = [93.0] + [1.0] * (n - 1)
    assert gini_simpson(uniform) == pytest.approx(max_gini_simpson(n))
    assert gini_simpson(skewed) < gini_simpson(uniform)


def test_assembly_cost_is_bounded_by_the_rate():
    # However many legs, the continuous cost cannot exceed the fee rate itself.
    for n in (2, 5, 20, 200):
        assert fee_ceiling_cents(n) < 7.0
    assert fee_ceiling_cents(200) == pytest.approx(7.0 * (1 - 1 / 200))


def test_every_leg_pays_at_least_a_cent():
    prices = [88.0] + [1.0] * 11
    assert bracket_fee_cents(1, prices) == 12
    # And the floor stays in force until the continuous term overtakes it.
    assert bracket_fee_cents(1, prices) / 1 > continuous_fee_cents_per_bracket(prices)


def test_floor_binding_size_matches_the_fee_function():
    price = 1.0
    size = floor_binding_size(price)
    assert fee_cents(int(np.floor(size)), price) == 1
    assert fee_cents(int(np.ceil(size)) + 1, price) > 1


def test_cheaper_structure_reverses_with_order_size():
    # Twelve legs, one of them nearly certain: low dispersion, high leg count.
    concentrated = [88.0] + [1.0] * 11
    # Three legs, all near a third: high dispersion, low leg count.
    dispersed = [34.0, 33.0, 33.0]

    small = assembly_cost(concentrated, 1), assembly_cost(dispersed, 1)
    large = assembly_cost(concentrated, 1000), assembly_cost(dispersed, 1000)

    assert small[0].per_bracket_cents > small[1].per_bracket_cents
    assert large[0].per_bracket_cents < large[1].per_bracket_cents

    size = crossover_size(concentrated, dispersed)
    assert size is not None and 1 < size < 20


def test_cost_curve_falls_towards_the_continuous_limit():
    prices = [88.0] + [1.0] * 11
    sizes = [1, 10, 100, 1000, 10000]
    curve = cost_curve(prices, sizes)
    assert np.all(np.diff(curve) <= 0)
    assert curve[-1] == pytest.approx(continuous_fee_cents_per_bracket(prices), abs=0.01)


def test_slicing_a_leg_costs_more_than_sending_it_whole():
    prices = [50.0, 50.0]
    whole = bracket_fee_cents(100, prices, orders_per_leg=1)
    sliced = bracket_fee_cents(100, prices, orders_per_leg=4)
    assert sliced >= whole


def test_floor_premium_vanishes_at_scale():
    prices = [70.0, 20.0, 10.0]
    assert assembly_cost(prices, 1).floor_premium_cents > 0
    assert assembly_cost(prices, 100000).floor_premium_cents == pytest.approx(0, abs=0.001)


def test_prices_may_be_given_in_dollars():
    assert fee_cents(100, 0.5, unit="dollars") == fee_cents(100, 50, unit="cents")
