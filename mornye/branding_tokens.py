"""branding_tokens.py — Mornye's palette, the one copy both interfaces share.

The look is "warm dark music app": warm near-black neutrals (no blue cast,
unlike the old Apple greys), artwork as the loudest thing on screen, and the
teal of the logo as the accent. A warm amber highlight exists for the rare
thing that should glow — a Hi-Res badge, "new" — and is used sparingly.

Two consumers, two mechanisms:

* The GUI's ``mornye/frontend/css/tokens.css`` is generated from the token
  v2 tables below (``python -m mornye.branding_tokens``);
  ``tests/test_brand_tokens_v2.py`` fails when the two differ. The v1 names
  of DARK/LIGHT left the GUI with styles.css (redesign voce 22).
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
    "brand-deep": BRAND_DEEP,  # behind the logo mark (sidebar, Home hero)
    # The desktop window's own close/minimise/maximise dots: the platform's
    # colours, macOS and then Windows, not the app's.
    "dot-close": "#ff5f57",
    "dot-min": "#febc2e",
    "dot-max": "#28c840",
    "dot-close-win": "#e81828",
    "dot-min-win": "#f0ad4e",
    "dot-max-win": "#28a745",
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


# ===========================================================================
# Token v2 — "Console da studio" (docs/redesign-plan.md §2, §3, voce 8)
#
# What the redesign builds on. The v1 palette above stays until Fase 3 (GUI)
# and Fase 4 (TUI) move off it. Two levels: primitives are raw values with
# no meaning; semantic tokens say what a value is for, per theme, and are
# the only thing the interfaces use. frontend/css/tokens.css is generated
# from here (python -m mornye.branding_tokens) and a test keeps it equal.
# ===========================================================================

#: Raw values. Graphite with no warm or blue cast, its "paper" counterpart
#: for the light theme, one amber signal, and two state colours per theme.
V2_PRIMITIVES: dict[str, str] = {
    "grey-0": "#0d0d0d",
    "grey-1": "#141414",
    "grey-2": "#1b1b1b",
    "grey-3": "#242424",
    "grey-4": "#2c2c2c",
    "grey-5": "#3d3d3d",
    "grey-6": "#6b6b6b",
    "grey-7": "#8c8c8c",
    "grey-8": "#b5b5b5",
    "grey-9": "#e8e8e8",
    "paper-0": "#f2f2f0",
    "paper-1": "#fafafa",
    "paper-2": "#efefed",
    "paper-3": "#e4e4e2",
    "paper-4": "#dcdcda",
    "paper-5": "#c4c4c2",
    "paper-6": "#8a8a88",
    "paper-7": "#666664",
    "paper-8": "#3d3d3d",
    "paper-9": "#141414",
    "amber-400": "#ffb000",
    "amber-500": "#e8a000",
    "amber-700": "#b06d00",
    "amber-800": "#8a5700",
    "amber-ink": "#120d00",
    "green-on-dark": "#5ec46b",
    "green-on-light": "#1d7a39",
    "red-on-dark": "#ff5a4f",
    "red-on-light": "#c4362c",
}

#: Semantic token -> primitive, per theme.
#:
#: The signal has three jobs that need three contrasts, which only coincide
#: on dark: ``signal`` fills (a primary button, with ``signal-ink`` on it),
#: ``signal-strong`` draws (focus ring, selection bar, lit meter segment:
#: 3:1 against the surface), ``signal-text`` writes (4.5:1).
V2_SEMANTIC: dict[str, dict[str, str]] = {
    "dark": {
        "ground": "grey-0",
        "surface": "grey-1",
        "raised": "grey-2",
        "selected": "grey-3",
        "rule": "grey-4",
        "rule-strong": "grey-5",
        "control-border": "grey-6",
        "text": "grey-9",
        "text-2": "grey-8",
        "text-muted": "grey-7",
        "signal": "amber-400",
        "signal-strong": "amber-400",
        "signal-text": "amber-400",
        "signal-ink": "amber-ink",
        "ok": "green-on-dark",
        "error": "red-on-dark",
    },
    "light": {
        "ground": "paper-0",
        "surface": "paper-1",
        "raised": "paper-2",
        "selected": "paper-3",
        "rule": "paper-4",
        "rule-strong": "paper-5",
        "control-border": "paper-6",
        "text": "paper-9",
        "text-2": "paper-8",
        "text-muted": "paper-7",
        "signal": "amber-500",
        "signal-strong": "amber-700",
        "signal-text": "amber-800",
        "signal-ink": "amber-ink",
        "ok": "green-on-light",
        "error": "red-on-light",
    },
}

#: Everything that is not a colour. Same in both themes.
V2_SPACE: dict[str, str] = {
    "space-1": "4px",
    "space-2": "8px",
    "space-3": "12px",
    "space-4": "16px",
    "space-5": "20px",
    "space-6": "24px",
    "space-8": "32px",
}
V2_SIZE: dict[str, str] = {
    "row": "32px",
    "row-head": "28px",
    "titlebar": "32px",
    "dock": "40px",
    "rail": "176px",
}
V2_RADIUS: dict[str, str] = {"radius-1": "2px", "radius-2": "3px"}
V2_TYPE: dict[str, str] = {
    "font": "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
    "text-xs": "10.5px",
    "text-sm": "12px",
    "text-md": "13px",
    "text-lg": "15px",
    "text-xl": "18px",
    "text-2xl": "22px",
    "weight-regular": "400",
    "weight-medium": "500",
    "weight-bold": "700",
    "leading": "1.4",
    # 0.12em, not the .14em first planned: the GUI has shown 0.12em since the
    # bridge (styles.css's own value won over tokens.css), and voce 22 kept
    # the look as it was.
    "tracking-label": "0.12em",
}
#: Per theme, but not a primitive: a translucent veil or an image. The scrim
#: dims the page behind a modal or a drawer, the overlay is a chip laid over
#: artwork, the chevron is the arrow drawn on every <select> (the one control
#: the browser would otherwise draw in its own chrome). Values carried over
#: unchanged from styles.css (voce 22).
_CHEVRON = (
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 24 24' fill='none' stroke='%23{}' stroke-width='2.4' "
    "stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline "
    "points='6 9 12 15 18 9'/%3E%3C/svg%3E\")"
)
V2_THEMED: dict[str, dict[str, str]] = {
    "dark": {
        "scrim": "rgba(10,8,7,.62)",
        "overlay": "rgba(10,8,7,.72)",
        "chevron": _CHEVRON.format("9a8f85"),
    },
    "light": {
        "scrim": "rgba(40,30,22,.36)",
        "overlay": "rgba(20,14,10,.62)",
        "chevron": _CHEVRON.format("6f655c"),
    },
}

#: Flat by rule: separation is a 1px rule. An overlay (menu, modal) is told
#: apart by its border, not a shadow.
V2_ELEVATION: dict[str, str] = {
    "elevation-0": "none",
    "elevation-overlay": "0 0 0 1px var(--rule-strong)",
}
#: State changes only, never entrances; nothing longer than 120ms.
V2_MOTION: dict[str, str] = {
    "motion-fast": "80ms",
    "motion": "120ms",
    "ease": "cubic-bezier(.2, 0, 0, 1)",
}

#: (foreground, background, minimum ratio) that must hold in both themes:
#: 4.5:1 for text, 3:1 for what identifies a control or carries a value.
V2_CONTRAST: tuple[tuple[str, str, float], ...] = (
    ("text", "ground", 4.5),
    ("text", "surface", 4.5),
    ("text", "selected", 4.5),
    ("text-2", "surface", 4.5),
    ("text-muted", "surface", 4.5),
    ("text-muted", "raised", 4.5),
    ("text-muted", "selected", 4.5),
    ("signal-text", "surface", 4.5),
    ("signal-text", "selected", 4.5),
    ("signal-ink", "signal", 4.5),
    ("ok", "surface", 4.5),
    ("error", "surface", 4.5),
    ("signal-strong", "surface", 3.0),
    ("signal-strong", "selected", 3.0),
    ("control-border", "surface", 3.0),
    ("control-border", "raised", 3.0),
)


def v2_tokens(mode: str) -> dict[str, str]:
    """The semantic tokens of ``mode`` ("dark" or "light"), as hex values."""
    return {name: V2_PRIMITIVES[ref] for name, ref in V2_SEMANTIC[mode].items()}


def v2_textual_theme_kwargs(mode: str) -> dict:
    """``textual.theme.Theme`` arguments for the console look (Fase 4).

    Registered first by mornye/tui/app.py (voce 24). ``$primary`` (focus, active mode, panel title) is the
    signal; ``$panel`` is the selected row, one step above the surface.
    """
    dark = mode != "light"
    t = v2_tokens("dark" if dark else "light")
    return {
        "name": "mornye-console-dark" if dark else "mornye-console-light",
        "primary": t["signal-strong"],
        "secondary": t["text-2"],
        "accent": t["signal-text"],
        "warning": t["signal-text"],
        "error": t["error"],
        "success": t["ok"],
        "foreground": t["text"],
        "background": t["ground"],
        "surface": t["surface"],
        "panel": t["selected"],
        "boost": t["raised"],
        "dark": dark,
        "variables": {
            "text-muted": t["text-muted"],
            "border": t["rule-strong"],
            "border-blurred": t["rule"],
            "footer-key-foreground": t["signal-text"],
            "input-selection-background": t["signal"] + "40",
        },
    }


def v2_tokens_css() -> str:
    """frontend/css/tokens.css, exactly. Regenerate with
    ``python -m mornye.branding_tokens``."""

    def block(selector: str, values: dict[str, str]) -> list[str]:
        return [f"{selector} {{", *(f"  --{k}: {v};" for k, v in values.items()), "}"]

    def semantic(mode: str) -> dict[str, str]:
        return {name: f"var(--{ref})" for name, ref in V2_SEMANTIC[mode].items()}

    lines = [
        "/* frontend/css/tokens.css — token v2, generated from",
        "   mornye/branding_tokens.py. Do not edit: change the module and run",
        "   `python -m mornye.branding_tokens`; tests/test_brand_tokens.py fails",
        "   when the two differ. Interfaces use the semantic names only. */",
        "",
        *block(
            ":root",
            {
                **V2_PRIMITIVES,
                **V2_SPACE,
                **V2_SIZE,
                **V2_RADIUS,
                **V2_TYPE,
                **V2_ELEVATION,
                **V2_MOTION,
                # Theme-independent (Now Playing's ink, a barcode's paper):
                # the same in both themes, so declared once here.
                **FIXED,
                **FIXED_CHANNELS,
                **semantic("dark"),
                **V2_THEMED["dark"],
            },
        ),
        "",
        *block(
            "html.light-theme,\nbody.light-theme",
            {**semantic("light"), **V2_THEMED["light"]},
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    from pathlib import Path

    target = Path(__file__).with_name("frontend") / "css" / "tokens.css"
    target.parent.mkdir(exist_ok=True)
    target.write_text(v2_tokens_css(), encoding="utf-8")
    print(f"wrote {target}")
