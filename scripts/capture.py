"""Render the command line output used in the write-up.

The images in the README are the real output of the real commands, captured by
running them. Regenerate with::

    python scripts/capture.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brackets.report import render_terminal  # noqa: E402

OUT = ROOT / "docs" / "img"

SHOTS = [
    ("board", ["board", "--max-pages", "40"], "shot_board.png",
     "$ brackets board"),
    ("screen", ["screen", "--panel", "data/quotes", "--out", "results"],
     "shot_screen.png", "$ brackets screen"),
]


def run(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "brackets.cli", *args],
        cwd=ROOT, capture_output=True, text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT / "src")},
    )
    return (result.stdout or "") + (result.stderr or "")


def main() -> int:
    wanted = set(sys.argv[1:])
    for name, args, filename, title in SHOTS:
        if wanted and name not in wanted:
            continue
        text = run(args).rstrip()
        path = render_terminal(text, OUT, filename, title=title)
        print(f"{name}: {len(text.splitlines())} lines -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
