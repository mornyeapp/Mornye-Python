"""The GUI and the TUI share one palette (mornye/branding_tokens.py).

The GUI's stylesheets build on css/tokens.css, generated from the module
(token v2). Since redesign voce 22 removed styles.css, no sheet names the
old v1 tokens any more; branding_tokens.DARK/LIGHT remain the TUI's first
themes. These tests keep that true, keep the theme-independent tokens
declared once, and keep the pairs people read legible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mornye import branding_tokens as bt

FRONTEND = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
TOKENS_CSS = (FRONTEND / "css" / "tokens.css").read_text(encoding="utf-8")
#: The GUI's own sheets: everything under css/ but the generated tokens, the
#: accent presets and the music look's values, which define values rather
#: than use them.
SHEETS = {
    str(p.relative_to(FRONTEND)): p.read_text(encoding="utf-8")
    for p in sorted((FRONTEND / "css").rglob("*.css"))
    if p.name not in ("tokens.css", "accents.css", "music-tokens.css")
}
V2_NAMES = set(bt.V2_SEMANTIC["dark"]) | {
    *bt.V2_SPACE,
    *bt.V2_SIZE,
    *bt.V2_RADIUS,
    *bt.V2_TYPE,
    *bt.V2_ELEVATION,
    *bt.V2_MOTION,
    *bt.V2_THEMED["dark"],
    *bt.FIXED,
    *bt.FIXED_CHANNELS,
}


def _vars(block: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2).strip().lower()
        for m in re.finditer(r"--([\w-]+):\s*([^;]+);", block)
    }


def test_no_sheet_names_a_v1_token() -> None:
    """Voce 22: the bridge from the old names to token v2 is gone, so a
    sheet that still said var(--muted) would get nothing at all."""
    v1 = {name for name in (*bt.DARK, *bt.LIGHT) if name not in V2_NAMES}
    left = {
        sheet: sorted(v1 & set(re.findall(r"var\(--([\w-]+)", css)))
        for sheet, css in SHEETS.items()
    }
    assert not {k: v for k, v in left.items() if v}


@pytest.mark.parametrize(("mode", "tokens"), [("dark", bt.DARK), ("light", bt.LIGHT)])
def test_text_pairs_meet_wcag_aa(mode: str, tokens: dict) -> None:
    weak = {
        f"{fg} on {bg}": round(bt.contrast_ratio(tokens[fg], tokens[bg]), 2)
        for fg, bg in bt.CONTRAST_PAIRS
        if bt.contrast_ratio(tokens[fg], tokens[bg]) < 4.5
    }
    assert not weak, f"{mode}: below 4.5:1 — {weak}"


def test_amber_is_the_default_accent_everywhere() -> None:
    """Voce 28: the console signal is the default; an accent sets the
    --signal tokens and nothing else."""
    from mornye.app import MornyeAPI

    frontend = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
    app_js = (frontend / "app.js").read_text(encoding="utf-8")
    accents_css = (frontend / "css" / "accents.css").read_text(encoding="utf-8")
    js_accents = re.search(r"const ACCENTS = \[(.*?)\];", app_js).group(1)
    assert [a.strip(" '") for a in js_accents.split(",")] == list(MornyeAPI.ACCENTS)
    assert MornyeAPI.ACCENTS[0] == "amber"
    assert 'data-accent="amber"' not in accents_css, "amber is tokens.css itself"
    # Every other accent has a dark and a light block, of signal tokens only.
    for accent in MornyeAPI.ACCENTS[1:]:
        assert f'html[data-accent="{accent}"]' in accents_css
        assert f'html.light-theme[data-accent="{accent}"]' in accents_css
    assert "--accent" not in accents_css.split("*/", 1)[1]
    carrying = [sheet for sheet, css in SHEETS.items() if "data-accent" in css]
    assert not carrying, f"only accents.css carries presets: {carrying}"


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_every_accent_meets_wcag_aa(mode: str) -> None:
    """Voce 30: an accent is four signal values; each must still read."""
    css = (
        Path(__file__).resolve().parents[1]
        / "mornye"
        / "frontend"
        / "css"
        / "accents.css"
    ).read_text(encoding="utf-8")
    surface = bt.v2_tokens(mode)["surface"]
    light = r"\.light-theme" if mode == "light" else ""
    weak = {}
    for name, body in re.findall(
        rf'html{light}\[data-accent="(\w+)"\][^{{]*\{{([^}}]*)\}}', css
    ):
        v = dict(re.findall(r"--(signal[\w-]*):\s*([^;]+);", body))
        pairs = {
            "text/surface": (bt.contrast_ratio(v["signal-text"], surface), 4.5),
            "ink/signal": (bt.contrast_ratio(v["signal-ink"], v["signal"]), 4.5),
            "strong/surface": (bt.contrast_ratio(v["signal-strong"], surface), 3.0),
        }
        weak.update(
            {f"{name} {k}": round(r, 2) for k, (r, floor) in pairs.items() if r < floor}
        )
    assert not weak, f"{mode}: {weak}"


def test_textual_themes_build_from_the_tokens() -> None:
    from textual.theme import Theme

    for mode in ("dark", "light"):
        theme = Theme(**bt.textual_theme_kwargs(mode))
        source = bt.DARK if mode == "dark" else bt.LIGHT
        assert theme.name == f"mornye-{mode}"
        assert theme.primary.lower() == source["accent"]
        assert theme.background.lower() == source["bg"]


def test_theme_independent_tokens_are_declared_once() -> None:
    """Now Playing's ink, a barcode's paper: the same in both themes."""
    root_block, light_block = TOKENS_CSS.split("html.light-theme", 1)
    root, light = _vars(root_block), _vars(light_block)
    fixed = {**bt.FIXED, **bt.FIXED_CHANNELS}
    drift = {
        name: (value, root.get(name))
        for name, value in fixed.items()
        if root.get(name) != value.lower()
    }
    assert not drift, f"token -> (branding_tokens, tokens.css :root) differ: {drift}"
    assert not set(fixed) & set(light), "the light theme overrides a fixed token"
