"""Band arithmetic, the two sides of the bracket, and the triage."""

import numpy as np
import pandas as pd
import pytest

from brackets.diagnostics import funnel, triage
from brackets.panel import align, prepare
from brackets.screen import ScreenConfig, naive_flags, screen_ladders, screen_partitions
from brackets.simulate import DEFAULT_LEGS, simulate_panel, simulated_universe
from brackets.structure import classify


def build(minutes=120, **kwargs):
    panel, planted = simulate_panel(minutes=minutes, **kwargs)
    structures = {"SIM-EVENT": classify(simulated_universe()[0])}
    prepared = prepare(panel)
    aligned, _ = align(prepared, {"SIM-EVENT": [t for t, *_ in DEFAULT_LEGS]})
    return aligned, structures, planted


def test_the_simulated_event_classifies_as_a_proven_partition():
    _, structures, _ = build(minutes=5)
    assert structures["SIM-EVENT"].screenable


def test_band_half_width_is_the_sum_of_half_spreads():
    aligned, structures, _ = build(minutes=10)
    screened = screen_partitions(aligned, structures)
    # Six legs quoted a cent wide: half a cent each, three cents of band.
    assert screened["band"].round(6).eq(3.0).all()
    assert screened["n_legs"].eq(6).all()


def test_the_two_gross_edges_are_the_mid_sum_against_the_band():
    aligned, structures, _ = build(minutes=20)
    screened = screen_partitions(aligned, structures)
    long_gross = 100.0 - screened["mid_sum"] - screened["band"]
    short_gross = screened["mid_sum"] - screened["band"] - 100.0
    assert np.allclose(screened["long_gross"], long_gross)
    assert np.allclose(screened["short_gross"], short_gross)
    # And they cannot both be positive, which is what makes the band a band.
    assert not ((screened["long_gross"] > 0) & (screened["short_gross"] > 0)).any()


def test_a_fairly_quoted_partition_sits_inside_its_band():
    # Nothing planted: no frozen leg, no thin offer, no dislocation. Every
    # minute should sit inside the band, on both sides.
    aligned, structures, _ = build(minutes=60, arbitrage_at=-1, stale_for=0, thin_at=-1)
    screened = screen_partitions(aligned, structures)
    assert (screened["long_gross"] <= 0).all()
    assert (screened["short_gross"] <= 0).all()


def test_the_planted_dislocation_is_found_and_survives_fees():
    aligned, structures, planted = build(minutes=120)
    screened = screen_partitions(aligned, structures)
    flags = naive_flags(screened)
    hit = flags[flags["ts"].isin(planted.arbitrage_minutes)]
    assert len(hit) == 1
    assert hit.iloc[0]["side"] == "long"
    assert hit.iloc[0]["gross"] > 0
    # Six legs of a bracket whose mass is concentrated: the fee is well under a
    # cent per bracket at size, so a four-cent dislocation clears it easily.
    assert hit.iloc[0]["net_1000"] > 0


def test_the_planted_stale_leg_is_attributed_to_staleness():
    aligned, structures, planted = build(minutes=120)
    flags = naive_flags(screen_partitions(aligned, structures))
    triaged = triage(flags, structures, ScreenConfig())
    stale = triaged[triaged["ts"].isin(planted.stale_minutes)]
    assert len(stale) > 0
    assert (stale["reason"] == "stale_quote").all()


def test_triage_prefers_the_structural_reason_over_the_numerical_one():
    aligned, structures, _ = build(minutes=30)
    # Tell the screen this is a ladder and every flag should be charged to shape,
    # even though the quotes and fees are unchanged.
    from brackets.structure import Shape
    structures["SIM-EVENT"].shape = Shape.LADDER
    screened = screen_partitions(aligned, structures, require_proven=False)
    flags = naive_flags(screened)
    if len(flags):
        triaged = triage(flags, structures, ScreenConfig())
        assert (triaged["reason"] == "wrong_shape").all()


def test_depth_is_checked_on_the_side_that_actually_trades():
    # A bracket whose legs are all offered deep but where one leg has nobody
    # bidding for it. The long trade is executable; the short trade is not,
    # because there is no bid to sell that leg into.
    stamp = pd.Timestamp("2026-09-10 12:00", tz="UTC")
    rows = [
        {"ticker": "SIM-T85", "yes_bid": 60.0, "no_bid": 39.0,
         "yes_bid_size": 400.0, "yes_ask_size": 400.0},
        {"ticker": "SIM-B86", "yes_bid": 30.0, "no_bid": 69.0,
         "yes_bid_size": 400.0, "yes_ask_size": 400.0},
        {"ticker": "SIM-B88", "yes_bid": 12.0, "no_bid": 87.0,
         "yes_bid_size": 400.0, "yes_ask_size": 400.0},
        {"ticker": "SIM-B90", "yes_bid": 1.0, "no_bid": 98.0,
         "yes_bid_size": 400.0, "yes_ask_size": 400.0},
        {"ticker": "SIM-B92", "yes_bid": 1.0, "no_bid": 98.0,
         "yes_bid_size": 400.0, "yes_ask_size": 400.0},
        # Nobody bids for this leg at all, so it cannot be sold.
        {"ticker": "SIM-T92", "yes_bid": 0.0, "no_bid": 99.0,
         "yes_bid_size": 0.0, "yes_ask_size": 400.0},
    ]
    panel = prepare(pd.DataFrame([
        {"ts": stamp, "event_ticker": "SIM-EVENT", "status": "active", **row}
        for row in rows
    ]))
    structures = {"SIM-EVENT": classify(simulated_universe()[0])}
    screened = screen_partitions(panel, structures)

    row = screened.iloc[0]
    assert row["long_min_size"] == 400.0        # every leg can be bought
    assert row["short_min_size"] == 0.0         # one leg cannot be sold
    assert row["long_all_offered"]
    assert not row["short_all_offered"]

    # The bid sum is 104, so a side-blind screen calls this a four-cent short.
    assert row["bid_sum"] == 104.0
    flags = naive_flags(screened)
    assert flags.iloc[0]["side"] == "short"
    assert flags.iloc[0]["min_offer_size"] == 0.0
    triaged = triage(flags, structures, ScreenConfig())
    assert triaged.iloc[0]["reason"] == "no_depth"


def test_funnel_shares_add_to_one():
    aligned, structures, _ = build(minutes=120)
    flags = naive_flags(screen_partitions(aligned, structures))
    table = funnel(triage(flags, structures, ScreenConfig()))
    assert table["share"].sum() == pytest.approx(1.0)


def test_ladder_violation_needs_the_high_bid_above_the_low_ask():
    legs = [
        {"ticker": "L1", "floor_strike": 10.0, "rules_primary": "is above 10"},
        {"ticker": "L2", "floor_strike": 20.0, "rules_primary": "is above 20"},
        {"ticker": "L3", "floor_strike": 30.0, "rules_primary": "is above 30"},
    ]
    structure = classify({"event_ticker": "LAD", "markets": legs, "title": "index price"})

    stamp = pd.Timestamp("2026-09-10 12:00", tz="UTC")
    # L2's bid of 60 sits above L1's ask of 55, which cannot be right: a higher
    # threshold cannot be more likely than a lower one.
    rows = [
        {"ticker": "L1", "yes_bid": 50.0, "no_bid": 45.0},
        {"ticker": "L2", "yes_bid": 60.0, "no_bid": 35.0},
        {"ticker": "L3", "yes_bid": 20.0, "no_bid": 75.0},
    ]
    panel = prepare(pd.DataFrame([
        {"ts": stamp, "event_ticker": "LAD", "yes_ask_size": 500.0,
         "yes_bid_size": 500.0, **row} for row in rows
    ]))
    hits = screen_ladders(panel, {"LAD": structure})
    assert len(hits) == 1
    assert hits.iloc[0]["low_ticker"] == "L1"
    assert hits.iloc[0]["high_ticker"] == "L2"
    assert hits.iloc[0]["gross"] == pytest.approx(5.0)


def test_a_monotone_ladder_produces_nothing():
    legs = [{"ticker": f"L{i}", "floor_strike": 10.0 * i, "rules_primary": f"is above {10 * i}"}
            for i in (1, 2, 3)]
    structure = classify({"event_ticker": "LAD", "markets": legs, "title": "index price"})
    stamp = pd.Timestamp("2026-09-10 12:00", tz="UTC")
    rows = [{"ticker": "L1", "yes_bid": 70.0, "no_bid": 28.0},
            {"ticker": "L2", "yes_bid": 40.0, "no_bid": 58.0},
            {"ticker": "L3", "yes_bid": 10.0, "no_bid": 88.0}]
    panel = prepare(pd.DataFrame([
        {"ts": stamp, "event_ticker": "LAD", "yes_ask_size": 500.0,
         "yes_bid_size": 500.0, **row} for row in rows
    ]))
    assert len(screen_ladders(panel, {"LAD": structure})) == 0
