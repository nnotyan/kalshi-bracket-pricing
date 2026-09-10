"""Cut a small, committable slice of a collected panel.

The repository ships a sample so that `brackets screen` runs out of the box
without waiting on a collection window. This writes it from whatever is in
data/quotes.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brackets.collect import load_panel  # noqa: E402

SAMPLE = ROOT / "data" / "sample"


def main(minutes: int = 90) -> int:
    panel = load_panel(ROOT / "data" / "quotes")
    stamps = sorted(panel["ts"].unique())[-minutes:]
    slice_ = panel[panel["ts"].isin(stamps)]

    SAMPLE.mkdir(parents=True, exist_ok=True)
    out = SAMPLE / "quotes_sample.parquet"
    slice_.to_parquet(out, index=False, compression="zstd")
    shutil.copy(ROOT / "data" / "universe.json", SAMPLE / "universe.json")

    size_kb = out.stat().st_size / 1024
    print(f"{len(slice_):,} quotes, {slice_['ts'].nunique()} minutes, "
          f"{slice_['event_ticker'].nunique()} events -> {out.name} ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 90))
