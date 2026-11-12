"""Draw every TUI panel to SVG: the visual baseline for the redesign.

Fase 0, voce 2 of ``docs/redesign-plan.md``. Each of the twelve panels is
opened through the sidebar and exported with ``App.export_screenshot`` at
80×24 (the compact layout) and 120×40, in the default theme and in
``mornye-light``. The result is a folder of SVGs to compare before and after
a change — by eye, or with ``--compare`` against a saved baseline.

The app runs against an empty, throw-away home so that nothing from this
machine — history, installed extensions, saved sessions, paths — ends up in
a picture that may be committed.

    python tests/visual/capture_tui.py                 # → tests/visual/out/tui
    python tests/visual/capture_tui.py --out DIR
    python tests/visual/capture_tui.py --compare tests/visual/baseline/tui
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 40))
THEMES: tuple[str, ...] = ("mornye-dark", "mornye-light")

#: Settling cycles after switching panel. The slowest panels in the suite
#: (extensions, tracklist) need 15; a picture taken earlier is half drawn.
_SETTLE = 15


def _isolate(home: Path) -> None:
    """Point every path Mornye reads at an empty home, before it is imported."""
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ["MORNYE_DATA_DIR"] = str(home / ".mornye")
    os.environ["MORNYE_CACHE_DIR"] = str(home / ".mornye" / ".cache")
    # Shown in the Configuration panel; fixed so it reads the same anywhere.
    os.environ["MORNYE_DOWNLOAD_DIR"] = "/Music/Mornye"
    os.environ.pop("NO_COLOR", None)


def _normalise(svg: str, home: Path) -> str:
    """Strip what changes between runs without the look changing."""
    svg = svg.replace(str(home), "~")
    # The banner shows the release; a version bump is not a change of look.
    svg = re.sub(r"v\d+\.\d+\.\d+", "v0.0.0", svg)
    # Textual names the SVG's classes after a hash of the content.
    return re.sub(r"terminal-\d+", "terminal", svg)


async def _capture(out: Path, home: Path) -> list[Path]:
    from mornye.tui.app import MODES, MornyeTui
    from mornye.tui.config_state import ConfigState

    written: list[Path] = []
    for width, height in SIZES:
        for theme in THEMES:
            state = ConfigState(
                url="https://open.spotify.com/album/example",
                output_dir="/Music/Mornye",
                services=["tidal"],
            )
            app = MornyeTui(state)
            async with app.run_test(size=(width, height)) as pilot:
                app.theme = theme
                for _ in range(_SETTLE):
                    await pilot.pause()
                sidebar = app.query_one("#sidebar")
                for index, (key, _label) in enumerate(MODES):
                    sidebar.index = index  # type: ignore[attr-defined]
                    for _ in range(_SETTLE):
                        await pilot.pause()
                    name = f"{index:02d}-{key}_{width}x{height}_{theme}.svg"
                    path = out / name
                    svg = app.export_screenshot(title=f"{key} {width}x{height}")
                    path.write_text(_normalise(svg, home), encoding="utf-8")
                    written.append(path)
    return written


def _compare(out: Path, baseline: Path) -> int:
    changed = []
    for path in sorted(out.glob("*.svg")):
        ref = baseline / path.name
        if not ref.exists():
            changed.append(f"new      {path.name}")
        elif ref.read_text(encoding="utf-8") != path.read_text(encoding="utf-8"):
            changed.append(f"changed  {path.name}")
    for ref in sorted(baseline.glob("*.svg")):
        if not (out / ref.name).exists():
            changed.append(f"missing  {ref.name}")
    print("\n".join(changed) or "identical to the baseline")
    return 1 if changed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=HERE / "out" / "tui")
    parser.add_argument("--compare", type=Path, help="baseline folder to diff against")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.svg"):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix="mornye-visual-") as tmp:
        home = Path(tmp)
        _isolate(home)
        sys.path.insert(0, str(ROOT))
        written = asyncio.run(_capture(args.out, home))
    print(f"{len(written)} snapshots in {args.out}")
    return _compare(args.out, args.compare) if args.compare else 0


if __name__ == "__main__":
    raise SystemExit(main())
