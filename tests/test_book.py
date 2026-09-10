"""The inversion identity, which everything downstream is built on."""

import pandas as pd
import pytest

from brackets.book import (NO_OFFER, PAR_CENTS, add_derived_quotes, executable_ask,
                           invert, reconcile)


def test_no_bid_becomes_a_yes_offer():
    # A resting bid for NO at 89 is an offer to sell YES at 11: the two sides
    # together mint a pair worth exactly a dollar.
    quote = invert(yes_bid=10, no_bid=89)
    assert quote.yes_ask == 11
    assert quote.no_ask == 90
    assert quote.width == 1
    assert quote.half_spread == 0.5
    assert quote.mid == 10.5


def test_ask_sits_a_half_spread_above_mid():
    quote = invert(yes_bid=30, no_bid=64)
    assert quote.mid + quote.half_spread == pytest.approx(quote.yes_ask)
    assert quote.mid - quote.half_spread == pytest.approx(quote.yes_bid)


def test_empty_opposite_side_means_no_offer():
    quote = invert(yes_bid=42, no_bid=0)
    assert quote.yes_ask == NO_OFFER
    assert not quote.yes_offered
    assert quote.no_offered


def test_crossed_book_is_flagged():
    # Two bids adding to more than par cannot both be live; one is a stale read.
    quote = invert(yes_bid=60, no_bid=45)
    assert quote.crossed
    assert quote.width < 0


def test_frame_inversion_matches_scalar():
    frame = pd.DataFrame({"yes_bid": [10, 30, 42], "no_bid": [89, 64, 0]})
    out = add_derived_quotes(frame)
    for i, row in out.iterrows():
        quote = invert(frame.loc[i, "yes_bid"], frame.loc[i, "no_bid"])
        assert row["yes_ask_derived"] == quote.yes_ask
        assert row["half_spread"] == quote.half_spread
        assert bool(row["yes_offered"]) is quote.yes_offered


def test_reconcile_against_venue_reported_asks():
    frame = add_derived_quotes(pd.DataFrame({
        "yes_bid": [10, 30],
        "no_bid": [89, 64],
        "yes_ask": [11, 36],
    }))
    report = reconcile(frame)
    assert report["checked"] == 2
    assert report["mismatches"] == 0
    assert report["max_abs_diff"] == 0


def test_reconcile_notices_a_broken_identity():
    frame = add_derived_quotes(pd.DataFrame({
        "yes_bid": [10], "no_bid": [89], "yes_ask": [14],
    }))
    assert reconcile(frame)["mismatches"] == 1


def test_executable_ask_drops_offers_without_depth():
    frame = add_derived_quotes(pd.DataFrame({
        "yes_bid": [10, 10, 42],
        "no_bid": [89, 89, 0],
        "yes_ask_size": [500, 3, 900],
    }))
    asks = executable_ask(frame, min_size=25)
    assert asks.iloc[0] == 11        # deep enough to lift
    assert pd.isna(asks.iloc[1])     # quoted, but three contracts behind it
    assert pd.isna(asks.iloc[2])     # nothing offered at all


def test_par_is_a_dollar():
    assert PAR_CENTS == 100.0
