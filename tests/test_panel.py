"""Staleness measurement and cross-section alignment."""

import pandas as pd

from brackets.panel import align, event_quality, mark_competitive, prepare


def quote(stamp, ticker, yes_bid, no_bid, size=500.0, event="E1", status="active"):
    return {"ts": stamp, "event_ticker": event, "ticker": ticker,
            "yes_bid": yes_bid, "no_bid": no_bid, "yes_ask_size": size,
            "yes_bid_size": size, "volume_24h": 100.0, "status": status}


def minutes(n, start="2026-09-10 12:00"):
    return pd.date_range(start, periods=n, freq="min", tz="UTC")


def test_staleness_counts_minutes_since_either_bid_moved():
    stamps = minutes(5)
    rows = [quote(stamps[0], "A", 40, 59),
            quote(stamps[1], "A", 40, 59),
            quote(stamps[2], "A", 40, 59),
            quote(stamps[3], "A", 41, 58),
            quote(stamps[4], "A", 41, 58)]
    out = prepare(pd.DataFrame(rows))
    assert list(out["stale_minutes"]) == [0.0, 1.0, 2.0, 0.0, 1.0]


def test_staleness_is_wall_clock_not_poll_count():
    # A gap in collection counts as elapsed time, so a quote is not called fresh
    # over a stretch when nobody was looking at it.
    stamps = [pd.Timestamp("2026-09-10 12:00", tz="UTC"),
              pd.Timestamp("2026-09-10 12:30", tz="UTC")]
    out = prepare(pd.DataFrame([quote(stamps[0], "A", 40, 59), quote(stamps[1], "A", 40, 59)]))
    assert out["stale_minutes"].iloc[-1] == 30.0


def test_each_leg_is_tracked_separately():
    stamps = minutes(3)
    rows = []
    for i, stamp in enumerate(stamps):
        rows.append(quote(stamp, "A", 40 + i, 59 - i))   # moves every minute
        rows.append(quote(stamp, "B", 20, 79))           # never moves
    out = prepare(pd.DataFrame(rows)).set_index(["ticker", "ts"])
    assert out.loc[("A", stamps[2]), "stale_minutes"] == 0.0
    assert out.loc[("B", stamps[2]), "stale_minutes"] == 2.0


def test_incomplete_cross_sections_are_dropped_not_summed():
    stamps = minutes(3)
    rows = [quote(stamps[0], "A", 40, 59), quote(stamps[0], "B", 55, 44),
            quote(stamps[1], "A", 40, 59),                       # B missing
            quote(stamps[2], "A", 40, 59), quote(stamps[2], "B", 55, 44)]
    kept, report = align(prepare(pd.DataFrame(rows)), {"E1": ["A", "B"]})
    assert kept["ts"].nunique() == 2
    assert stamps[1] not in set(kept["ts"])
    assert report.loc[0, "dropped"] == 1


def test_a_closed_leg_makes_the_minute_incomplete():
    stamps = minutes(2)
    rows = [quote(stamps[0], "A", 40, 59), quote(stamps[0], "B", 55, 44),
            quote(stamps[1], "A", 40, 59), quote(stamps[1], "B", 55, 44, status="settled")]
    kept, _ = align(prepare(pd.DataFrame(rows)), {"E1": ["A", "B"]})
    assert kept["ts"].nunique() == 1


def test_events_outside_the_universe_are_reported_not_screened():
    stamps = minutes(1)
    rows = [quote(stamps[0], "A", 40, 59, event="UNKNOWN")]
    kept, report = align(prepare(pd.DataFrame(rows)), {"E1": ["A"]})
    assert len(kept) == 0
    assert report.loc[0, "reason"] == "not in universe"


def test_competitive_flag_needs_tightness_depth_and_two_sidedness():
    stamps = minutes(4)
    tight = [quote(s, "A", 40, 59, size=500) for s in stamps]
    wide = [quote(s, "B", 30, 60, size=500, event="E2") for s in stamps]
    shallow = [quote(s, "C", 40, 59, size=3, event="E3") for s in stamps]
    one_sided = [quote(s, "D", 40, 0, size=500, event="E4") for s in stamps]

    prepared = prepare(pd.DataFrame(tight + wide + shallow + one_sided))
    marked = mark_competitive(event_quality(prepared)).set_index("event_ticker")
    assert bool(marked.loc["E1", "competitive"])
    assert not bool(marked.loc["E2", "competitive"])   # ten cents wide
    assert not bool(marked.loc["E3", "competitive"])   # three contracts on the offer
    assert not bool(marked.loc["E4", "competitive"])   # nothing offered at all
