"""branding_tokens.py — Mornye's palette, the one copy both interfaces share.

The look is "warm dark music app": warm near-black neutrals (no blue cast,
unlike the old Apple greys), artwork as the loudest thing on screen, and the
teal of the logo as the accent. A warm amber highlight exists for the rare
thing that should glow — a Hi-Res badge, "new" — and is used sparingly.

Two consumers, two mechanisms:

* The GUI's ``mornye/frontend/styles.css`` declares the same values as CSS
  custom properties (``:root`` = dark, ``.light-theme`` = light). There is no
  build step, so ``tests/test_brand_tokens.py`` parses the stylesheet and
  fails if a value here and its ``--token`` there drift apart.
* The TUI builds two Textual themes, ``mornye-dark`` and ``mornye-light``,
  from :func:`textual_theme_kwargs` (see ``mornye/tui/app.py``).

Values are plain ``#rrggbb`` so they work in both places unchanged.
"""

from __future__ import annotations

#: Logo colours. Not themed: the artwork is the same in both modes.
BRAND_DEEP = "#1f4f4f"  # the petrol background of the icon
BRAND_MINT = "#a8ecec"  # the arrow

#: token name (the CSS custom property without ``--``) -> value.
DARK: dict[str, str] = {
    "bg": "#0e0c0b",
    "surface": "#181513",
    "surface2": "#221e1b",
    "surface3": "#2d2824",
    "border": "#332d29",
    "border2": "#4a423c",
    "text": "#f3eee8",
    "text2": "#d4cbc2",
    "muted": "#9a8f85",
    "text-dim": "#7d736a",
    "accent": "#5cd3c3",
    "accent-hover": "#7adfd1",
    "accent-press": "#45bfae",
    "accent-fg": "#04201c",
    "accent-text": "#6fdccd",
    "highlight": "#f0a560",
    "warning": "#e8c07a",
    "red": "#ff6b5e",
    "blue": "#6aa8ff",
    "green": "#5fd38a",
}

LIGHT: dict[str, str] = {
    "bg": "#f6f2ec",
    "surface": "#fffdf9",
    "surface2": "#f1ebe3",
    "surface3": "#e7dfd5",
    "border": "#e4dcd2",
    "border2": "#cbbfb2",
    "text": "#1d1916",
    "text2": "#3b342e",
    "muted": "#6f655c",
    "accent": "#12776b",
    "accent-hover": "#0f6a5f",
    "accent-press": "#0c5a51",
    "accent-fg": "#ffffff",
    "accent-text": "#0f6c61",
    "highlight": "#a8560f",
    "warning": "#8a6210",
    "red": "#c8392f",
    "blue": "#2c6fd1",
    "green": "#22844a",
}

#: Colours that do not follow the theme, because what sits behind them does
#: not: Now Playing is drawn over a tint taken from the artwork, a barcode is
#: black on white whatever the page is. Declared once in ``:root``; the light
#: theme must not override them (tests/test_brand_tokens.py).
FIXED: dict[str, str] = {
    "media-ink": "#ffffff",  # text and icons on Now Playing's artwork tint
    "media-ink-inverse": "#222222",  # on a media-ink chip (the active tab)
    "media-bg": "#2a1a1a",  # behind Now Playing before the tint is known
    "media-tint-from": "#6b3b3b",  # the default tint, top left …
    "media-tint-to": "#3a2323",  # … to bottom right
    "paper": "#ffffff",  # a barcode's ground
    "ink": "#000000",  # a barcode's bars; behind a spectrogram
    "on-signal": "#ffffff",  # the glyph on a coloured status dot
    "player-main-ink": "#0a0a0a",  # the glyph on the player's main button
}

#: The same, as ``r, g, b`` channels, for the translucent shades a design
#: needs of them: ``rgba(var(--media-ink-rgb), .62)``. (color-mix() would
#: read better, but older WebKitGTK builds do not have it.)
FIXED_CHANNELS: dict[str, str] = {
    "media-ink-rgb": "255, 255, 255",
    "shade-rgb": "0, 0, 0",
    "knob-shadow-rgb": "15, 23, 42",
}


#: The pairs that must stay readable (WCAG AA, 4.5:1 for body text). Checked
#: by tests/test_brand_tokens.py for both modes.
CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("text", "bg"),
    ("text", "surface"),
    ("text2", "surface"),
    ("muted", "surface"),
    ("accent-text", "surface"),
    ("accent-fg", "accent"),
)


def textual_theme_kwargs(mode: str) -> dict:
    """Keyword arguments for ``textual.theme.Theme`` in ``mode`` (dark/light).

    Mapping, chosen against how ``mornye/tui/mornye.tcss`` uses the variables:
    ``$primary`` is the wordmark and focus colour (teal), ``$accent`` paints
    the panel tags and the Atmos badge (warm highlight), ``$warning`` the
    Hi-Res badge (gold), ``$panel`` is the
    border/selection surface — one step above ``$surface``, which is the
    ground the whole app sits on.
    """
    dark = mode != "light"
    t = DARK if dark else LIGHT
    return {
        "name": "mornye-dark" if dark else "mornye-light",
        "primary": t["accent"],
        "secondary": BRAND_MINT if dark else BRAND_DEEP,
        "accent": t["highlight"],
        "warning": t["warning"],
        "error": t["red"],
        "success": t["green"],
        "foreground": t["text"],
        "background": t["bg"],
        "surface": t["surface"],
        "panel": t["surface3"],
        "boost": t["surface2"],
        "dark": dark,
        "variables": {
            "text-muted": t["muted"],
            "footer-key-foreground": t["accent"],
            "input-selection-background": t["accent"] + "40",
        },
    }


def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
