"""Command line entry points.

Five verbs, in the order they are normally run::

    brackets board      one-off structural census of every open event
    brackets discover   build the working universe from config/universe.yaml
    brackets collect    poll the universe once a minute into parquet
    brackets screen     run both screens over the panel and write the tables
    brackets explain    one event-minute end to end, with the arithmetic shown
    brackets report     draw the figures from the screen output
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

from .api import KalshiClient
from .book import reconcile
from .collect import collect, discover, load_panel, read_universe, write_universe
from .diagnostics import (band_profile, funnel, stale_leg_detail, survivors, triage)
from .fees import assembly_cost, bracket_fee_cents, gini_simpson
from .panel import align, event_quality, mark_competitive, prepare
from .screen import ScreenConfig, naive_flags, screen_ladders, screen_partitions

log = logging.getLogger("brackets")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _screen_config(config: dict) -> ScreenConfig:
    fees = config.get("fees", {})
    execution = config.get("execution", {})
    return ScreenConfig(
        fee_rate=float(fees.get("rate", 0.07)),
        order_sizes=tuple(fees.get("order_sizes", (1, 10, 50, 250, 1000))),
        min_offer_size=float(execution.get("min_offer_size", 25)),
        max_stale_minutes=float(execution.get("max_stale_minutes", 5)),
    )


def cmd_board(args: argparse.Namespace) -> int:
    """Classify every open event and report the census."""
    client = KalshiClient()
    entries = discover(client, min_legs=args.min_legs, max_pages=args.max_pages,
                       shapes=("partition", "ladder", "categorical"),
                       strict_support=args.strict_support)
    write_universe(entries, Path(args.out))

    census = Counter((e.shape, e.exhaustive) for e in entries)
    total = sum(census.values())

    # The full universe file is tens of megabytes; the census that gets quoted is
    # four columns, so write that separately and keep it in the repository.
    rows = [{"shape": shape, "exhaustive": exhaustive, "events": count,
             "share": count / total,
             "legs": sum(e.n_legs for e in entries
                         if e.shape == shape and e.exhaustive == exhaustive)}
            for (shape, exhaustive), count in census.most_common()]
    pd.DataFrame(rows).to_csv(Path(args.out).with_name("board_census.csv"), index=False)

    print(f"{total} multi-outcome events with at least {args.min_legs} legs\n")
    print(f"{'shape':<14}{'exhaustiveness':<17}{'events':>8}{'share':>9}{'legs':>9}")
    for (shape, exhaustive), count in census.most_common():
        legs = sum(e.n_legs for e in entries if e.shape == shape and e.exhaustive == exhaustive)
        print(f"{shape:<14}{exhaustive:<17}{count:>8}{count / total:>9.1%}{legs:>9,}")
    screenable = sum(1 for e in entries if e.screenable)
    print(f"\n{screenable} events ({screenable / total:.1%}) can be summed against par at all")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Build the working universe from the series listed in the config."""
    config = _load_yaml(Path(args.config))
    bounds = {k: tuple(v) for k, v in (config.get("domain_bounds") or {}).items()}
    default_cap = int(config.get("max_events_per_series", 2))
    client = KalshiClient()

    entries = []
    roles: dict[str, str] = {}
    for spec in config.get("series", []):
        ticker = spec["ticker"]
        cap = int(spec.get("max_events", default_cap))
        found = discover(client, series=[ticker], min_legs=args.min_legs,
                         shapes=("partition", "ladder", "categorical"),
                         domain_bounds=bounds)
        for entry in found[:cap]:
            roles[entry.event_ticker] = spec.get("role", "")
            entries.append(entry)

    write_universe(entries, Path(args.out))
    roles_path = Path(args.out).with_name("roles.json")
    roles_path.write_text(json.dumps(roles, indent=2), encoding="utf-8")

    print(f"{len(entries)} events, {sum(e.n_legs for e in entries)} legs -> {args.out}")
    for entry in entries:
        print(f"  {entry.event_ticker:<28} {entry.shape:<12}{entry.exhaustive:<12}"
              f"legs={entry.n_legs:<4}vol={entry.volume_24h:,.0f}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    entries = read_universe(Path(args.universe))
    collect(KalshiClient(), entries, Path(args.out),
            minutes=args.minutes, interval=args.interval)
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    config = _load_yaml(Path(args.config))
    screen_config = _screen_config(config)
    quality_config = config.get("quality", {})

    entries = read_universe(Path(args.universe))
    structures = {e.event_ticker: e.to_structure() for e in entries}
    expected = {e.event_ticker: e.tickers for e in entries}

    panel = prepare(load_panel(Path(args.panel)))
    aligned, alignment = align(panel, expected)
    log.info("panel: %d rows, %d after alignment", len(panel), len(aligned))

    # Cheapest possible guard against the venue changing what its price fields
    # mean: our derived asks and the ones Kalshi publishes must agree exactly.
    identity = reconcile(panel)
    if identity["mismatches"]:
        log.warning("book identity broken on %d of %d quotes (max %.2fc)",
                    identity["mismatches"], identity["checked"], identity["max_abs_diff"])

    quality = mark_competitive(
        event_quality(aligned),
        max_median_width=float(quality_config.get("max_median_width", 2.0)),
        min_offer_size=float(quality_config.get("min_offer_size", 25)),
        min_offered_share=float(quality_config.get("min_offered_share", 0.99)),
    )

    # The naive screen sums every event against par, including the ones that are
    # not partitions at all. That is the number the triage has to explain.
    naive = screen_partitions(aligned, structures, screen_config, require_proven=False)
    flags = naive_flags(naive, screen_config.order_sizes)
    triaged = triage(flags, structures, screen_config, screen_config.order_sizes)
    triaged = triaged.merge(quality[["event_ticker", "competitive"]],
                            on="event_ticker", how="left")

    ladders = screen_ladders(aligned, structures, screen_config)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    aligned.to_parquet(out / "panel_prepared.parquet", index=False)
    naive.to_parquet(out / "screen_partitions.parquet", index=False)
    triaged.to_parquet(out / "flags_triaged.parquet", index=False)
    alignment.to_csv(out / "alignment.csv", index=False)
    quality.to_csv(out / "event_quality.csv", index=False)
    band_profile(naive).to_csv(out / "band_profile.csv", index=False)
    funnel(triaged).to_csv(out / "funnel.csv", index=False)
    funnel(triaged, by="competitive").to_csv(out / "funnel_by_quality.csv", index=False)
    survivors(triaged).to_csv(out / "survivors.csv", index=False)
    stale_leg_detail(aligned, triaged[triaged["reason"] == "stale_quote"],
                     screen_config.max_stale_minutes).to_csv(out / "stale_legs.csv", index=False)
    ladders.to_csv(out / "ladder_violations.csv", index=False)

    _print_summary(aligned, naive, triaged, ladders, quality, identity)
    return 0


def _print_summary(panel: pd.DataFrame, screened: pd.DataFrame, triaged: pd.DataFrame,
                   ladders: pd.DataFrame, quality: pd.DataFrame,
                   identity: dict | None = None) -> None:
    minutes = panel["ts"].nunique()
    events = panel["event_ticker"].nunique()
    print(f"\npanel        {len(panel):,} quotes, {events} events, {minutes} minutes")
    if identity:
        print(f"book check   {identity['checked']:,} derived asks, "
              f"{identity['mismatches']} disagree with the venue's own")
    print(f"screened     {len(screened):,} event-minutes")
    print(f"raw flags    {len(triaged):,} "
          f"({len(triaged) / max(len(screened), 1):.1%} of event-minutes)")

    if len(triaged):
        print("\nwhere the raw flags come from")
        table = funnel(triaged)
        for row in table.itertuples():
            print(f"  {row.reason:<16}{row.flags:>7}{row.share:>9.1%}")

        competitive = triaged[triaged["competitive"].fillna(False)]
        if len(competitive):
            print(f"\nrestricted to competitively quoted events ({len(competitive)} flags)")
            for row in funnel(competitive).itertuples():
                print(f"  {row.reason:<16}{row.flags:>7}{row.share:>9.1%}")

    kept = survivors(triaged)
    print(f"\nsurvivors    {len(kept)}")
    if len(kept):
        print(kept.head(10).to_string(index=False))

    print(f"\nladder monotonicity violations: {len(ladders)}")
    if len(ladders):
        columns = [c for c in ladders.columns if c.startswith("net_")]
        print(f"  best gross {ladders['gross'].max():.2f}c, "
              f"best net {ladders[columns].to_numpy().max():.2f}c")

    tight = quality[quality["competitive"]]
    print(f"\ncompetitively quoted events: {len(tight)} of {len(quality)}")


def cmd_explain(args: argparse.Namespace) -> int:
    """Print one event-minute end to end, from leg quotes to net edge.

    Everything the screen does to a bracket, on a single cross-section, with the
    arithmetic shown rather than summarised. Useful for checking the pipeline
    against the venue by hand, and the fastest way to see why a bracket that
    looks cheap is not.
    """
    config = _load_yaml(Path(args.config))
    screen_config = _screen_config(config)

    entries = {e.event_ticker: e for e in read_universe(Path(args.universe))}
    if args.event not in entries:
        print(f"{args.event} is not in {args.universe}")
        return 1
    entry = entries[args.event]
    structure = entry.to_structure()

    panel = prepare(load_panel(Path(args.panel)))
    aligned, _ = align(panel, {args.event: entry.tickers})
    aligned = aligned[aligned["event_ticker"] == args.event]
    if not len(aligned):
        print(f"no complete cross-section for {args.event} in the panel")
        return 1

    stamp = (pd.Timestamp(args.at, tz="UTC") if args.at
             else sorted(aligned["ts"].unique())[-1])
    cross = aligned[aligned["ts"] == stamp].sort_values("yes_bid", ascending=False)

    print(f"\n{entry.event_ticker}   {structure.shape.value} / {structure.exhaustive.value}"
          f"   {len(cross)} legs")
    print(f"{entry.title}")
    for line in entry.evidence:
        print(f"  - {line}")
    print(f"\ncross-section at {stamp:%Y-%m-%d %H:%M} UTC\n")

    header = (f"{'leg':<14}{'outcome':<20}{'yes_bid':>8}{'no_bid':>8}{'ask':>7}"
              f"{'mid':>7}{'half':>7}{'offer':>8}{'stale':>7}")
    print(header)
    print("-" * len(header))
    labels = {leg.ticker: leg.label for leg in structure.legs}
    for row in cross.itertuples():
        print(f"{row.ticker.split('-')[-1]:<14}{labels.get(row.ticker, '')[:19]:<20}"
              f"{row.yes_bid:>8.0f}{row.no_bid:>8.0f}{row.yes_ask_derived:>7.0f}"
              f"{row.mid:>7.1f}{row.half_spread:>7.1f}"
              f"{(row.yes_ask_size or 0):>8.0f}{row.stale_minutes:>7.0f}")

    asks = cross["yes_ask_derived"].to_numpy(dtype="float64")
    bids = cross["yes_bid"].to_numpy(dtype="float64")
    band = float(cross["half_spread"].sum())
    mid_sum = float(cross["mid"].sum())
    ask_sum, bid_sum = float(asks.sum()), float(bids.sum())

    print("-" * len(header))
    print(f"{'sum of asks':<34}{ask_sum:>9.1f}   cost of buying the whole bracket")
    print(f"{'sum of mids':<34}{mid_sum:>9.1f}   par is 100.0")
    print(f"{'sum of bids':<34}{bid_sum:>9.1f}   return on selling the whole bracket")
    print(f"{'band half-width':<34}{band:>9.2f}   the accumulated half-spreads")
    print(f"{'no-arbitrage band':<34}{100 - band:>9.2f} to {100 + band:.2f}")
    print(f"\n{'gross long edge':<34}{100 - ask_sum:>9.2f}")
    print(f"{'gross short edge':<34}{bid_sum - 100:>9.2f}")
    print(f"{'Gini-Simpson index':<34}{gini_simpson(asks):>9.3f}   drives the fee")

    print(f"\n{'clip':>8}{'fee/bracket':>14}{'long net':>12}{'short net':>12}"
          f"{'floor legs':>12}")
    for size in screen_config.order_sizes:
        cost = assembly_cost(asks, size, rate=screen_config.fee_rate)
        short_fee = bracket_fee_cents(size, 100.0 - bids,
                                      rate=screen_config.fee_rate) / size
        print(f"{size:>8,}{cost.per_bracket_cents:>14.2f}"
              f"{100 - ask_sum - cost.per_bracket_cents:>12.2f}"
              f"{bid_sum - 100 - short_fee:>12.2f}{cost.floor_legs:>12}")
    print()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import build_report
    build_report(Path(args.results), Path(args.out), _load_yaml(Path(args.config)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brackets", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board", help="structural census of the open board")
    board.add_argument("--out", default="data/universe_all.json")
    board.add_argument("--min-legs", type=int, default=3)
    board.add_argument("--max-pages", type=int, default=40)
    board.add_argument("--strict-support", action="store_true",
                       help="accept only the support the rules text states, refusing to "
                            "read the settlement tick off the strike grid")
    board.set_defaults(func=cmd_board)

    disc = subparsers.add_parser("discover", help="build the working universe")
    disc.add_argument("--config", default="config/universe.yaml")
    disc.add_argument("--out", default="data/universe.json")
    disc.add_argument("--min-legs", type=int, default=3)
    disc.set_defaults(func=cmd_discover)

    coll = subparsers.add_parser("collect", help="poll quotes into parquet")
    coll.add_argument("--universe", default="data/universe.json")
    coll.add_argument("--out", default="data/quotes")
    coll.add_argument("--minutes", type=int, default=60)
    coll.add_argument("--interval", type=float, default=60.0)
    coll.set_defaults(func=cmd_collect)

    scr = subparsers.add_parser("screen", help="run the screens over a panel")
    scr.add_argument("--panel", default="data/quotes")
    scr.add_argument("--universe", default="data/universe.json")
    scr.add_argument("--config", default="config/screen.yaml")
    scr.add_argument("--out", default="results")
    scr.set_defaults(func=cmd_screen)

    exp = subparsers.add_parser("explain", help="one event-minute, end to end")
    exp.add_argument("event")
    exp.add_argument("--at", default=None, help="UTC timestamp; defaults to the last minute")
    exp.add_argument("--panel", default="data/quotes")
    exp.add_argument("--universe", default="data/universe.json")
    exp.add_argument("--config", default="config/screen.yaml")
    exp.set_defaults(func=cmd_explain)

    rep = subparsers.add_parser("report", help="draw the figures")
    rep.add_argument("--results", default="results")
    rep.add_argument("--config", default="config/screen.yaml")
    rep.add_argument("--out", default="docs/img")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    # Outcome labels carry degree signs and currency symbols, and the Windows
    # console defaults to a codepage that cannot print them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
