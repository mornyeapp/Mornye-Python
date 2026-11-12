"""Screenshot every GUI view: the visual baseline for the redesign.

Fase 0, voce 1 of ``docs/redesign-plan.md``. Starts ``mornye --web`` against
an empty, throw-away data folder (nothing from this machine reaches a
picture), opens it in Chrome through Playwright, and saves the ten views in
the dark and the light theme at three widths: a desktop window, 768 and 375.

Needs ``pip install playwright`` and Google Chrome (``channel="chrome"``, so
Playwright downloads no browser of its own). Not part of the test suite:
the pictures are compared by eye, or by name with ``--compare``, which only
reports which files differ — pixels move with fonts and anti-aliasing.

    python tests/visual/capture_gui.py                 # → tests/visual/out/gui
    python tests/visual/capture_gui.py --views home settings --widths 375
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

VIEWS: tuple[str, ...] = (
    "home",
    "explore",
    "player",
    "queue",
    "history",
    "local",
    "following",
    "stats",
    "logs",
    "settings",
)
#: name → (width, height). "desktop" is the default pywebview window.
WIDTHS: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "768": (768, 1024),
    "375": (375, 812),
}
THEMES: tuple[str, ...] = ("dark", "light")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"mornye --web exited early ({proc.returncode})")
        with (
            contextlib.suppress(OSError),
            socket.create_connection(("127.0.0.1", port), timeout=1),
        ):
            return
        time.sleep(0.3)
    raise SystemExit("mornye --web did not start in time")


@contextlib.contextmanager
def _server(home: Path):
    port = _free_port()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "MORNYE_DATA_DIR": str(home / ".mornye"),
        "MORNYE_CACHE_DIR": str(home / ".mornye" / ".cache"),
        "MORNYE_DOWNLOAD_DIR": "/Music/Mornye",
        "MORNYE_EXT_AUTO_INSTALL": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mornye", "--web", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(port, proc)
        yield f"http://127.0.0.1:{port}/"
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)


def _capture(url: str, out: Path, views, widths, themes) -> list[Path]:
    from playwright.sync_api import sync_playwright

    written: list[Path] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            for width_name in widths:
                width, height = WIDTHS[width_name]
                for theme in themes:
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        color_scheme=theme,  # type: ignore[arg-type]
                        reduced_motion="reduce",
                    )
                    page.add_init_script(
                        f"try {{ localStorage.setItem('mornye-theme-mode', '{theme}'); }}"
                        " catch (e) {}"
                    )
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_function("typeof switchView === 'function'")
                    # The URL box types its example links out one character
                    # at a time; freeze it, or every Home picture differs.
                    page.evaluate(
                        "() => { clearTimeout(phTimeout); runTypewriter = () => {};"
                        " $('urlInput').placeholder = 'open.spotify.com/track/…'; }"
                    )
                    for view in views:
                        page.evaluate(
                            "([v, t]) => { switchView(v); applyTheme(t); }",
                            [view, theme],
                        )
                        page.wait_for_timeout(600)
                        path = out / f"{view}_{width_name}_{theme}.png"
                        page.screenshot(path=str(path), full_page=False)
                        written.append(path)
                    page.close()
        finally:
            browser.close()
    return written


#: Per-channel difference below which a pixel counts as unchanged. Two runs
#: of the same page differ by a pixel or two of anti-aliasing (measured: at
#: most 16 of 255); a real change of colour or layout is far above it.
_NOISE = 16


def _differs(a: Path, b: Path) -> bool:
    if a.read_bytes() == b.read_bytes():
        return False
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return (
            hashlib.sha256(a.read_bytes()).digest()
            != hashlib.sha256(b.read_bytes()).digest()
        )
    with Image.open(a) as left, Image.open(b) as right:
        if left.size != right.size:
            return True
        diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        # The largest channel difference anywhere in the picture.
        return max(high for _low, high in diff.getextrema()) > _NOISE


def _compare(out: Path, baseline: Path) -> int:
    changed = []
    for path in sorted(out.glob("*.png")):
        ref = baseline / path.name
        if not ref.exists():
            changed.append(f"new      {path.name}")
        elif _differs(ref, path):
            changed.append(f"changed  {path.name}")
    for ref in sorted(baseline.glob("*.png")):
        if not (out / ref.name).exists():
            changed.append(f"missing  {ref.name}")
    print("\n".join(changed) or "identical to the baseline")
    return 1 if changed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=HERE / "out" / "gui")
    parser.add_argument("--views", nargs="+", choices=VIEWS, default=list(VIEWS))
    parser.add_argument(
        "--widths", nargs="+", choices=list(WIDTHS), default=list(WIDTHS)
    )
    parser.add_argument("--themes", nargs="+", choices=THEMES, default=list(THEMES))
    parser.add_argument("--compare", type=Path, help="baseline folder to diff against")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.png"):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix="mornye-visual-") as tmp:
        with _server(Path(tmp)) as url:
            written = _capture(url, args.out, args.views, args.widths, args.themes)
    print(f"{len(written)} screenshots in {args.out}")
    return _compare(args.out, args.compare) if args.compare else 0


if __name__ == "__main__":
    raise SystemExit(main())
