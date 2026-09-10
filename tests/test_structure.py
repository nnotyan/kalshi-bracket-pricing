"""Telling partitions from ladders, and proving coverage from settlement terms."""

import math

from brackets.structure import (Exhaustive, Shape, classify, infer_support,
                                ladder_to_buckets, leg_interval)


def market(ticker, floor=None, cap=None, rules="", label="", status="active"):
    return {"ticker": ticker, "floor_strike": floor, "cap_strike": cap,
            "rules_primary": rules, "yes_sub_title": label, "status": status}


def event(ticker, markets, **kwargs):
    return {"event_ticker": ticker, "markets": markets, **kwargs}


# Modelled on KXHIGHNY: whole-degree buckets with open tails at both ends. The
# interior buckets jump 85-86 then 87-88, which only tiles because the
# settlement source reports whole degrees.
WEATHER = event("KXHIGHNY-TEST", [
    market("T85", cap=85, rules="If the maximum temperature is less than 85°", label="84° or below"),
    market("B85.5", floor=85, cap=86, label="85° to 86°"),
    market("B87.5", floor=87, cap=88, label="87° to 88°"),
    market("B89.5", floor=89, cap=90, label="89° to 90°"),
    market("T90", floor=90, rules="If the maximum temperature is above 90°", label="91° or above"),
], mutually_exclusive=True, collateral_return_type="MECNET",
   title="Highest temperature in NYC today?")

# Modelled on KXBTCD: nested upward thresholds a hundred dollars apart.
LADDER = event("KXBTCD-TEST", [
    market(f"T{s}", floor=s, rules=f"If the index price is above {s}",
           label=f"${s + 0.01:,.0f} or above")
    for s in (70099.99, 70199.99, 70299.99, 70399.99)
], mutually_exclusive=False, collateral_return_type="DIRECNET",
   title="Bitcoin price at 1 PM?")


def test_weather_buckets_are_a_proven_partition():
    result = classify(WEATHER)
    assert result.shape is Shape.PARTITION
    assert result.exhaustive is Exhaustive.PROVEN
    assert result.screenable
    assert result.support.is_integer


def test_the_same_bucket_spacing_on_a_finer_grid_leaves_holes():
    # Identical one-unit jumps between buckets, but the strike grid is written
    # to the cent, so the settlement source can report 86.5 and nothing lists it.
    markets = [market("T85", cap=84.99, rules="If the closing index is less than 84.99"),
               market("B85.5", floor=85, cap=86),
               market("B87.5", floor=87, cap=88),
               market("T88", floor=88, rules="If the closing index is above 88")]
    result = classify(event("IDX-TEST", markets, title="Closing index price?"))
    assert result.support.step == 0.01
    assert result.exhaustive is Exhaustive.GAP
    assert not result.screenable
    assert "gap" in " ".join(result.evidence)


def test_strike_precision_is_read_as_the_settlement_tick():
    # The .99 convention: 4,199.99 and 4,200 touch because the index is
    # published to the cent, and the strikes are written that way to say so.
    markets = [market("T4200", cap=4199.99, rules="If the close is less than 4199.99"),
               market("B4250", floor=4200, cap=4299.99),
               market("T4300", floor=4299.99, rules="If the close is above 4299.99")]
    result = classify(event("KXINXY-TEST", markets, title="S&P close price?"))
    assert result.support.step == 0.01
    assert result.exhaustive is Exhaustive.PROVEN


def test_strict_support_refuses_to_read_the_tick_off_the_strikes():
    # The same S&P bracket. By default the .99 convention establishes a cent
    # tick and the legs tile; under the strict model, where only the rules text
    # counts, the one-cent step between 4,199.99 and 4,200 is an unproven gap.
    markets = [market("T4200", cap=4199.99, rules="If the close is less than 4199.99"),
               market("B4250", floor=4200, cap=4299.99),
               market("T4300", floor=4299.99, rules="If the close is above 4299.99")]
    event_dict = event("KXINXY-TEST", markets, title="S&P close price?")
    assert classify(event_dict).exhaustive is Exhaustive.PROVEN
    assert classify(event_dict, strict_support=True).exhaustive is Exhaustive.GAP


def test_nested_thresholds_are_a_ladder_not_a_partition():
    result = classify(LADDER)
    assert result.shape is Shape.LADDER
    assert result.exhaustive is Exhaustive.NOT_APPLICABLE
    # This is the whole point: a ladder must never reach the sum-against-par screen.
    assert not result.screenable


def test_ladder_differences_into_adjacent_bucket_pairs():
    pairs = ladder_to_buckets(classify(LADDER))
    assert len(pairs) == 3
    assert pairs[0] == ("T70099.99", "T70199.99")
    assert ladder_to_buckets(classify(WEATHER)) == []


def test_counts_do_not_need_an_open_bottom_tail():
    markets = [market(f"S{i}", floor=i, cap=i, label=str(i)) for i in range(0, 4)]
    markets.append(market("S4", floor=4, rules="If the number of cuts is above 4"))
    result = classify(event("CUTS-TEST", markets, title="How many rate cuts in 2026?"))
    assert result.support.lo_bound == 0.0
    assert result.exhaustive is Exhaustive.PROVEN


def test_a_closed_top_leg_on_an_unbounded_count_is_not_exhaustive():
    markets = [market(f"S{i}", floor=i, cap=i, label=str(i)) for i in range(0, 5)]
    result = classify(event("CUTS-TEST", markets, title="How many rate cuts in 2026?"))
    assert result.exhaustive is Exhaustive.OPEN_TAIL
    assert not result.screenable
    assert "above 4" in " ".join(result.evidence)


def test_configured_bounds_are_recorded_as_an_assumption():
    markets = [market(f"S{i}", floor=i, cap=i, label=str(i)) for i in range(0, 5)]
    ev = event("SEATS-TEST", markets, title="How many seats?", series_ticker="SEATS")
    result = classify(ev, domain_bounds={"SEATS": (0.0, 4.0)})
    assert result.exhaustive is Exhaustive.PROVEN
    assert "supplied by configuration" in " ".join(result.evidence)


def test_overlapping_legs_are_not_even_exclusive():
    markets = [market("A", cap=10), market("B", floor=5, cap=20), market("C", floor=8)]
    result = classify(event("OVER-TEST", markets, title="Closing price?"))
    assert result.exhaustive is Exhaustive.OVERLAP
    assert not result.screenable


def test_categorical_events_need_a_catch_all_leg():
    named = [market("A", label="Alice"), market("B", label="Bob"), market("C", label="Carol")]
    assert classify(event("WHO", named, title="Who wins?")).exhaustive is Exhaustive.UNPROVEN

    with_field = named + [market("D", label="Any other candidate")]
    result = classify(event("WHO", with_field, title="Who wins?", mutually_exclusive=True))
    assert result.shape is Shape.CATEGORICAL
    assert result.exhaustive is Exhaustive.RESIDUAL_LEG
    assert result.screenable


def test_categorical_exclusivity_is_not_assumed_without_the_venue_flag():
    # Labels alone cannot rule out two named outcomes both settling YES, so an
    # event the venue does not call mutually exclusive stays out of the screen.
    legs = [market("A", label="Alice"), market("B", label="Bob"),
            market("C", label="Any other candidate")]
    result = classify(event("WHO", legs, title="Who wins?", mutually_exclusive=False))
    assert result.exhaustive is Exhaustive.RESIDUAL_LEG
    assert not result.screenable


def test_rules_text_that_contradicts_the_strike_fields_is_reported():
    markets = [market("T85", cap=85, rules="If the index is less than 90"),
               market("B85.5", floor=85, cap=86),
               market("T86", floor=86, rules="If the index is above 86")]
    result = classify(event("BAD", markets, title="Closing index?"))
    assert "contradict" in " ".join(result.evidence)


def test_closed_legs_are_ignored():
    markets = list(WEATHER["markets"])
    markets[0] = dict(markets[0], status="settled")
    result = classify(event("KXHIGHNY-TEST", markets, title="Highest temperature in NYC today?"))
    # Losing the bottom tail turns a proven partition into an incomplete one.
    assert result.exhaustive is Exhaustive.OPEN_TAIL


def test_venue_flag_is_corroboration_not_proof():
    result = classify(WEATHER)
    assert result.venue_mutually_exclusive is True
    assert result.venue_agrees is True
    # A ladder the venue happens to flag as exclusive still classifies as a ladder.
    mislabelled = dict(LADDER, mutually_exclusive=True)
    assert classify(mislabelled).shape is Shape.LADDER
    assert classify(mislabelled).venue_agrees is False


def test_leg_intervals_follow_the_strike_convention():
    assert leg_interval({"floor_strike": 5, "cap_strike": 9}) == (5, 9)
    assert leg_interval({"floor_strike": 5}) == (5, math.inf)
    assert leg_interval({"cap_strike": 9}) == (-math.inf, 9)
    assert leg_interval({}) == (-math.inf, math.inf)


def test_support_inference_prefers_the_rules_text():
    degrees = infer_support([{"rules_primary": "the maximum temperature in °F",
                              "floor_strike": 85.5}])
    assert degrees.is_integer and degrees.step == 1.0
