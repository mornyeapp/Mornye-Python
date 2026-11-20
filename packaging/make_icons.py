"""Generate the app icons the desktop packages need from the frontend logo.

    python packaging/make_icons.py [OUT_DIR]       (default: build/icons)

Writes:

* ``mornye.icns`` — the macOS app icon. ``logo.png`` is a full-bleed square;
  macOS icons sit on a grid (an 824 pt rounded square inset 100 pt in a
  1024 pt canvas), so it is drawn the same way desktop_branding draws the
  Dock icon of a running app, or the bundle's icon in Finder would look
  bigger than every other app's.
* ``mornye.png`` — 512 px, the AppImage's icon (Linux themes do their own
  shaping, so this one stays full-bleed).

Windows uses ``frontend/assets/icons/logo.ico`` as it is.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mornye.desktop_branding import (  # noqa: E402
    _CANVAS,
    _CORNER_RADIUS,
    _INSET,
    app_icon_path,
)

_ICNS_SIZES = [
    (16, 16),
    (32, 32),
    (64, 64),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
]


def macos_icon(source: Image.Image) -> Image.Image:
    canvas = int(_CANVAS)
    inset = int(_INSET)
    body = canvas - 2 * inset
    # Drawn at 4x and scaled down, for smooth rounded corners.
    scale = 4
    mask = Image.new("L", (body * scale, body * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, body * scale - 1, body * scale - 1),
        radius=round(_CORNER_RADIUS * scale),
        fill=255,
    )
    mask = mask.resize((body, body), Image.Resampling.LANCZOS)

    logo = source.convert("RGBA").resize((body, body), Image.Resampling.LANCZOS)
    logo.putalpha(mask)

    icon = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    icon.paste(logo, (inset, inset), logo)
    return icon


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(app_icon_path()) as source:
        source.load()
        macos_icon(source).save(out_dir / "mornye.icns", sizes=_ICNS_SIZES)
        source.convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS).save(
            out_dir / "mornye.png"
        )

    for name in ("mornye.icns", "mornye.png"):
        print(out_dir / name)


if __name__ == "__main__":
    main()
