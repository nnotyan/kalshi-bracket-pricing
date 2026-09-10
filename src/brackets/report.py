"""Figures.

Two kinds. The schematics explain the two ideas the arithmetic rests on -- how a
bid-only book becomes a bid-ask pair, and how a partition differs from a ladder
-- and are drawn from nothing but the definitions. The rest are drawn from a
collected panel and say what actually happened in it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle

from .fees import continuous_fee_cents_per_bracket, cost_curve, crossover_size, gini_simpson

log = logging.getLogger(__name__)

INK = "#1c1c1c"
MUTED = "#8a8a8a"
GRID = "#e2e2e2"
YES = "#2c6fbb"
NO = "#c1553b"
ACCENT = "#3f8f5e"
WARN = "#d09a2c"
FIGSIZE = (9.0, 5.0)
DPI = 150

REASON_LABELS = {
    "wrong_shape": "not a partition",
    "not_exhaustive": "legs do not cover\nthe outcome space",
    "crossed_book": "crossed book",
    "stale_quote": "stale leg",
    "no_depth": "no depth on the offer",
    "fee_bound": "real, but smaller\nthan the fee",
    "survives": "survives",
}
REASON_COLOURS = {
    "wrong_shape": "#7a5c9e", "not_exhaustive": "#b0447a", "crossed_book": "#6b6b6b",
    "stale_quote": NO, "no_depth": WARN, "fee_bound": "#5d8aa8", "survives": ACCENT,
}


def _style(ax, title: str = "", xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=12)
    ax.set_xlabel(xlabel, fontsize=10, color=INK)
    ax.set_ylabel(ylabel, fontsize=10, color=INK)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def _save(fig, out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("wrote %s", path)
    return path


# -- schematics ------------------------------------------------------------

def figure_book_inversion(out: Path) -> Path:
    """How the two bid sides of a Kalshi book become a tradeable bid-ask pair."""
    fig, ax = plt.subplots(figsize=(9.4, 4.4))
    yes_bid, no_bid = 37.0, 61.0
    yes_ask = 100.0 - no_bid
    bar = 0.13

    rows = [
        (0.76, 0, yes_bid, YES, f"resting YES bids, best at {yes_bid:.0f}c"),
        (0.50, yes_ask, 100.0, NO, f"resting NO bids, best at {no_bid:.0f}c"),
    ]
    for y, lo, hi, colour, label in rows:
        ax.add_patch(Rectangle((lo, y), hi - lo, bar, facecolor=colour, alpha=0.8,
                               edgecolor="white", linewidth=0.8))
        ax.text((lo + hi) / 2, y + bar / 2, label, ha="center", va="center",
                color="white", fontsize=9.5)

    ax.text(100.5, 0.50 + bar / 2, "priced from the right\nedge of the dollar",
            fontsize=8.5, color=MUTED, va="center")

    # The constructed two-sided market.
    ax.add_patch(Rectangle((yes_bid, 0.20), yes_ask - yes_bid, bar, facecolor=GRID,
                           edgecolor=MUTED, hatch="////", linewidth=0.8))
    ax.plot([0, yes_bid], [0.20 + bar / 2] * 2, color=YES, linewidth=2.4, alpha=0.5)
    ax.plot([yes_ask, 100], [0.20 + bar / 2] * 2, color=NO, linewidth=2.4, alpha=0.5)
    ax.text(yes_bid - 2, 0.20 + bar + 0.015, f"bid {yes_bid:.0f}", ha="right", va="bottom",
            fontsize=9.5, color=YES)
    ax.text(yes_ask + 2, 0.20 + bar + 0.015, f"ask {yes_ask:.0f}", ha="left", va="bottom",
            fontsize=9.5, color=NO)
    ax.text(100.5, 0.20 + bar / 2, "the two-sided market\nthat results",
            fontsize=8.5, color=MUTED, va="center")
    ax.text((yes_bid + yes_ask) / 2, 0.155,
            f"spread {100 - yes_bid - no_bid:.0f}c", ha="center", va="top",
            fontsize=8.5, color=MUTED)

    for x, colour in ((yes_bid, YES), (yes_ask, NO)):
        ax.plot([x, x], [0.20, 0.89], color=colour, linewidth=0.9, linestyle=":", alpha=0.7)

    ax.add_patch(FancyArrowPatch((yes_ask + 4, 0.50), (yes_ask + 4, 0.20 + bar),
                                 arrowstyle="-|>", color=NO, linewidth=1.3,
                                 mutation_scale=13,
                                 connectionstyle="arc3,rad=0.35"))

    ax.text(0, 0.05,
            "A NO bid at 61c is an offer to sell YES at 100 - 61 = 39c. The venue mints the "
            "pair for a dollar\nand each side pays what it bid, so the YES ask is never "
            "quoted -- it is read off the other book.",
            fontsize=9.5, color=INK, va="top")

    ax.set_xlim(-1, 118)
    ax.set_ylim(-0.12, 1.0)
    ax.set_yticks([])
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xlabel("price of one YES contract, cents", fontsize=10, color=INK)
    ax.set_title("Kalshi runs a bid-only book; the asks are derived",
                 loc="left", fontsize=12.5, color=INK, pad=12)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_bounds(0, 100)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    return _save(fig, out, "book_inversion.png")


def figure_structure_taxonomy(out: Path) -> Path:
    """Disjoint buckets against nested thresholds, on the same axis."""
    fig, axes = plt.subplots(2, 1, figsize=(9.0, 4.6), sharex=True)

    buckets = [(-0.5, 1.0, "84 or below"), (1.0, 2.0, "85-86"), (2.0, 3.0, "87-88"),
               (3.0, 4.0, "89-90"), (4.0, 5.0, "91-92"), (5.0, 6.5, "93 or above")]
    for i, (lo, hi, label) in enumerate(buckets):
        axes[0].add_patch(Rectangle((lo, 0.3), hi - lo, 0.4, facecolor=YES,
                                    alpha=0.35 + 0.09 * (i % 2), edgecolor="white"))
        axes[0].text((lo + hi) / 2, 0.5, label, ha="center", va="center", fontsize=8.5,
                     color=INK)
    axes[0].text(-0.45, 0.86, "partition: exactly one leg settles YES, so the implied "
                 "probabilities sum to one", fontsize=10, color=INK)
    axes[0].text(-0.45, 0.06, "buying every leg below par is risk free", fontsize=9,
                 color=ACCENT)

    for i, start in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        y = 0.74 - i * 0.125
        axes[1].add_patch(Rectangle((start, y - 0.043), 6.5 - start, 0.086, facecolor=NO,
                                    alpha=0.55, edgecolor="white"))
        axes[1].text(start - 0.08, y, f"above {83 + 2 * int(start)}", ha="right",
                     va="center", fontsize=8.5, color=INK)
    axes[1].text(-0.45, 0.92, "ladder: the legs nest, so nothing constrains the sum "
                 "-- only the ordering", fontsize=10, color=INK)
    axes[1].text(-0.45, 0.04, "summing these against par is the mistake the screen refuses "
                 "to make", fontsize=9, color=NO)

    for ax in axes:
        ax.set_xlim(-0.5, 6.5)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(colors=MUTED, labelsize=9)
    axes[1].set_xticks(range(0, 7))
    axes[1].set_xticklabels(["", "85", "87", "89", "91", "93", ""])
    axes[1].set_xlabel("settlement variable", fontsize=10, color=INK)
    fig.suptitle("Two shapes that share an event ticker", x=0.125, ha="left",
                 fontsize=12, color=INK)
    return _save(fig, out, "structure_taxonomy.png")


# -- data figures ----------------------------------------------------------

def figure_band_vs_legs(profile: pd.DataFrame, out: Path) -> Path:
    """Band half-width against leg count, holding quoting quality fixed.

    Two things drive the band and they have to be separated. Leg count is the
    mechanical one: the same per-leg half-spread bought n times is n times the
    band. Quoting quality is the other, and it varies by an order of magnitude
    across the venue. The guide lines fix quality so that the leg-count effect
    is visible on its own, and the colour of each point says where that event
    actually sits.
    """
    fig, ax = plt.subplots(figsize=(9.4, 5.2))

    grid = np.linspace(profile["n_legs"].min() - 1, profile["n_legs"].max() + 7, 60)
    for per_leg, label_at in ((0.5, 0.90), (1.0, 0.90), (2.0, 0.90), (4.0, 0.90)):
        ax.plot(grid, per_leg * grid, color=MUTED, linestyle="--", linewidth=0.9,
                alpha=0.6, zorder=1)
        x = grid[int(len(grid) * label_at)]
        ax.text(x, per_leg * x, f" {per_leg:g}c per leg", fontsize=8, color=MUTED,
                rotation=0, va="bottom", ha="left")

    points = ax.scatter(profile["n_legs"], profile["median_band"], s=58,
                        c=profile["median_half_spread_per_leg"], cmap="viridis_r",
                        norm=matplotlib.colors.LogNorm(), edgecolor="white",
                        linewidth=0.8, zorder=3)
    bar = fig.colorbar(points, ax=ax, pad=0.015)
    bar.set_label("median half-spread per leg, cents", fontsize=9, color=INK)
    bar.ax.tick_params(colors=MUTED, labelsize=8)

    # Label the large brackets and the extremes; the six-leg weather events sit on
    # top of each other and are called out as a group instead.
    notable = profile[profile["n_legs"] >= 10].sort_values("n_legs")
    for i, row in enumerate(notable.itertuples()):
        ax.annotate(row.event_ticker.split("-")[0], (row.n_legs, row.median_band),
                    textcoords="offset points", xytext=(7, 5 if i % 2 else -9),
                    fontsize=7.5, color=INK)
    six = profile[profile["n_legs"] == 6]
    if len(six):
        ax.annotate(f"{len(six)} six-leg brackets",
                    (6, float(six["median_band"].median())),
                    textcoords="offset points", xytext=(12, 16), fontsize=8, color=INK,
                    arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.8))

    ax.set_yscale("log")
    _style(ax, "The no-arbitrage band is paid for one leg at a time",
           "legs in the partition", "band half-width, cents (log scale)")
    ax.set_xlim(grid[0], grid[-1])
    return _save(fig, out, "band_vs_legs.png")


def figure_mid_sum_band(screened: pd.DataFrame, out: Path,
                        event_ticker: str | None = None) -> Path:
    """One event's mid sum against its own band, minute by minute."""
    def pick(subset: pd.DataFrame) -> str | None:
        if not len(subset):
            return None
        ranked = (subset.groupby("event_ticker")
                  .agg(minutes=("ts", "nunique"), band=("band", "median")))
        ranked = ranked[ranked["minutes"] >= ranked["minutes"].max() * 0.9]
        return str(ranked["band"].idxmin())

    proven = event_ticker or pick(screened[screened["exhaustive"] == "proven"])
    incomplete = pick(screened[screened["exhaustive"] == "open_tail"])
    chosen = [(proven, "legs proven to cover every outcome"),
              (incomplete, "legs that leave outcomes unlisted")]
    chosen = [(t, s) for t, s in chosen if t]

    fig, axes = plt.subplots(1, len(chosen), figsize=(10.4, 4.6), squeeze=False)
    for ax, (ticker, subtitle) in zip(axes[0], chosen):
        frame = screened[screened["event_ticker"] == ticker].sort_values("ts")
        ax.fill_between(frame["ts"], 100 - frame["band"], 100 + frame["band"],
                        color=YES, alpha=0.14, label="no-arbitrage band")
        ax.axhline(100, color=MUTED, linewidth=1.0, linestyle="--")
        ax.plot(frame["ts"], frame["ask_sum"], color=NO, linewidth=1.2,
                label="sum of asks (cost to buy)")
        ax.plot(frame["ts"], frame["mid_sum"], color=INK, linewidth=1.4,
                label="sum of mids")
        ax.plot(frame["ts"], frame["bid_sum"], color=YES, linewidth=1.2,
                label="sum of bids (return on selling)")

        band = float(frame["band"].median())
        legs = int(frame["n_legs"].iloc[0])
        _style(ax, f"{ticker}\n{subtitle}", "", "cents" if ax is axes[0][0] else "")
        ax.set_title(f"{ticker}\n{subtitle}", loc="left", fontsize=10.5, color=INK, pad=10)
        ax.text(0.02, 0.02, f"{legs} legs, band half-width {band:.2f}c",
                transform=ax.transAxes, fontsize=8.5, color=MUTED)
        ax.margins(y=0.22)
        ax.tick_params(axis="x", labelrotation=35, labelsize=8)

    axes[0][0].legend(frameon=False, fontsize=8.5, loc="upper left", ncols=2)
    fig.suptitle("The mid sum stays inside the band -- unless the bracket is incomplete",
                 x=0.09, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, out, "mid_sum_band.png")


def figure_flag_funnel(triaged: pd.DataFrame, out: Path) -> Path:
    """Where the raw flags come from, overall and on the tight books."""
    def counts(frame):
        return frame["reason"].value_counts()

    order = [r for r in REASON_LABELS if r in set(triaged["reason"])]
    overall = counts(triaged).reindex(order).fillna(0)
    competitive = triaged[triaged["competitive"].fillna(False).astype(bool)]
    tight = counts(competitive).reindex(order).fillna(0)

    fig, ax = plt.subplots(figsize=(9.0, 0.62 * len(order) + 2.0))
    y = np.arange(len(order))
    total_all = max(overall.sum(), 1)
    total_tight = max(tight.sum(), 1)

    ax.barh(y + 0.19, overall / total_all, height=0.36,
            color=[REASON_COLOURS[r] for r in order], alpha=0.85)
    ax.barh(y - 0.19, tight / total_tight, height=0.36,
            color=[REASON_COLOURS[r] for r in order], alpha=0.42)

    for i, reason in enumerate(order):
        ax.text(overall[reason] / total_all + 0.008, i + 0.19,
                f"{int(overall[reason]):,}", va="center", fontsize=8.5, color=INK)
        ax.text(tight[reason] / total_tight + 0.008, i - 0.19,
                f"{int(tight[reason]):,}", va="center", fontsize=8.5, color=MUTED)

    ax.set_yticks(y)
    ax.set_yticklabels([REASON_LABELS[r] for r in order], fontsize=9)
    ax.invert_yaxis()
    _style(ax, f"What {len(triaged):,} raw flags actually were",
           "share of raw flags", "")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    # Each pair of bars is a share of its own denominator, so the legend has to
    # carry the two counts or the comparison reads as a level rather than a mix.
    handles = [Patch(facecolor=MUTED, alpha=0.85,
                     label=f"all events (n = {int(overall.sum()):,})"),
               Patch(facecolor=MUTED, alpha=0.42,
                     label=f"competitively quoted only (n = {int(tight.sum()):,})")]
    ax.legend(handles=handles, frameon=False, fontsize=9, loc="lower right")
    return _save(fig, out, "flag_funnel.png")


def figure_fee_identity(screened: pd.DataFrame, out: Path, rate: float = 0.07) -> Path:
    """Assembly cost against the Gini-Simpson index of the implied distribution."""
    sample = screened.dropna(subset=["gini_simpson"])
    if len(sample) > 4000:
        sample = sample.iloc[:: len(sample) // 4000 + 1]

    largest_size = max(
        (int(c.split("_")[-1]) for c in screened.columns if c.startswith("long_fee_")),
        default=1000)
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.scatter(sample["gini_simpson"], sample[f"long_fee_{largest_size}"], s=8,
               color=YES, alpha=0.28, edgecolor="none",
               label=f"observed, {largest_size:,}-contract clip")

    grid = np.linspace(0, 1, 100)
    ax.plot(grid, rate * 100 * grid, color=INK, linewidth=1.4,
            label=f"{rate:.0%} x Gini-Simpson index")

    for n in (3, 6, 12, 30):
        ax.axvline(1 - 1 / n, color=MUTED, linewidth=0.7, linestyle=":")
        ax.text(1 - 1 / n, rate * 100 * 1.02, f"n={n}", rotation=90, fontsize=7.5,
                color=MUTED, ha="right", va="top")

    _style(ax, "Assembly cost is dispersion, not edge",
           "Gini-Simpson index of the implied distribution",
           "fee per bracket, cents")
    ax.set_xlim(0, 1)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    return _save(fig, out, "fee_identity.png")


def figure_fee_size_crossover(out: Path, rate: float = 0.07,
                              concentrated: list[float] | None = None,
                              dispersed: list[float] | None = None) -> Path:
    """Per-bracket cost against order size for two partition shapes."""
    concentrated = concentrated or ([88.0] + [1.0] * 11)
    dispersed = dispersed or [34.0, 33.0, 33.0]
    sizes = np.unique(np.round(np.logspace(0, 3.4, 90)).astype(int))

    fig, ax = plt.subplots(figsize=FIGSIZE)
    curve_a = cost_curve(concentrated, sizes, rate=rate)
    curve_b = cost_curve(dispersed, sizes, rate=rate)
    ax.plot(sizes, curve_a, color=YES, linewidth=1.6,
            label=f"12 legs, one near-certain (D={gini_simpson(concentrated):.2f})")
    ax.plot(sizes, curve_b, color=NO, linewidth=1.6,
            label=f"3 legs, near uniform (D={gini_simpson(dispersed):.2f})")
    ax.axhline(continuous_fee_cents_per_bracket(concentrated, rate=rate), color=YES,
               linestyle=":", linewidth=1.0)
    ax.axhline(continuous_fee_cents_per_bracket(dispersed, rate=rate), color=NO,
               linestyle=":", linewidth=1.0)

    cross = crossover_size(concentrated, dispersed, rate=rate)
    if cross:
        ax.axvline(cross, color=INK, linewidth=1.0, linestyle="--")
        ax.annotate(f"the cheaper structure\nswaps at {cross} contracts",
                    xy=(cross, curve_a[np.searchsorted(sizes, cross)]),
                    xytext=(cross * 2.4, max(curve_a[0], curve_b[0]) * 0.55),
                    fontsize=9, color=INK,
                    arrowprops=dict(arrowstyle="->", color=INK, linewidth=1.0))

    ax.set_xscale("log")
    _style(ax, "The per-order rounding decides which bracket is cheaper",
           "contracts per leg", "fee per bracket, cents")
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    return _save(fig, out, "fee_size_crossover.png")


def figure_board_census(universe_path: Path, out: Path) -> Path:
    """The structural make-up of every open multi-outcome event on the board."""
    import json
    rows = json.loads(Path(universe_path).read_text(encoding="utf-8"))["events"]
    frame = pd.DataFrame([{"shape": r["shape"], "exhaustive": r["exhaustive"],
                           "screenable": r["screenable"]} for r in rows])
    counts = (frame.groupby(["shape", "exhaustive"]).size()
              .sort_values(ascending=False).reset_index(name="events"))

    fig, ax = plt.subplots(figsize=(9.0, 0.55 * len(counts) + 1.8))
    labels = [f"{r.shape}  /  {r.exhaustive.replace('_', ' ')}" for r in counts.itertuples()]
    colours = [ACCENT if r.exhaustive in ("proven", "residual_leg") else MUTED
               for r in counts.itertuples()]
    ax.barh(labels, counts["events"], color=colours, alpha=0.85)
    for i, value in enumerate(counts["events"]):
        ax.text(value * 1.02, i, f"{value:,}", va="center", fontsize=9, color=INK)
    ax.invert_yaxis()
    ax.set_xscale("log")
    _style(ax, f"{len(frame):,} open multi-outcome events, by what they actually are",
           "events (log scale)", "")
    return _save(fig, out, "board_census.png")


def render_terminal(text: str, out: Path, name: str, title: str = "") -> Path:
    """Render captured command output as an image, for the write-up."""
    lines = text.rstrip("\n").split("\n")
    height = 0.176 * len(lines) + 0.62
    fig = plt.figure(figsize=(10.5, height), facecolor="#1b1d21")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#1b1d21")
    ax.axis("off")
    if title:
        ax.text(0.014, 1 - 0.28 / height, title, fontsize=9.5, color="#7f8791",
                family="monospace", va="top")
    ax.text(0.014, 1 - (0.62 if title else 0.28) / height, "\n".join(lines),
            fontsize=8.6, color="#d7dae0", family="monospace", va="top", linespacing=1.42)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    fig.savefig(path, dpi=DPI, facecolor="#1b1d21")
    plt.close(fig)
    return path


def build_report(results: Path, out: Path, config: dict | None = None) -> list[Path]:
    """Draw everything that the available inputs support."""
    config = config or {}
    rate = float((config.get("fees") or {}).get("rate", 0.07))
    written = [figure_book_inversion(out), figure_structure_taxonomy(out),
               figure_fee_size_crossover(out, rate=rate)]

    screened_path = results / "screen_partitions.parquet"
    if screened_path.exists():
        screened = pd.read_parquet(screened_path)
        if len(screened):
            from .diagnostics import band_profile
            partitions = screened[screened["shape"] == "partition"]
            if len(partitions):
                written.append(figure_band_vs_legs(band_profile(partitions), out))
                written.append(figure_mid_sum_band(partitions, out))
                written.append(figure_fee_identity(partitions, out, rate=rate))

    flags_path = results / "flags_triaged.parquet"
    if flags_path.exists():
        triaged = pd.read_parquet(flags_path)
        if len(triaged):
            written.append(figure_flag_funnel(triaged, out))

    census = Path("data/universe_all.json")
    if census.exists():
        written.append(figure_board_census(census, out))

    return written
