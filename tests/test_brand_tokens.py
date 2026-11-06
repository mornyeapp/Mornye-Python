"""The GUI and the TUI share one palette (mornye/branding_tokens.py).

There is no build step between the token module and the stylesheet, so this
is what keeps them one identity: every token the module defines must appear
with the same value in styles.css (``:root`` for dark, ``.light-theme`` for
light), and the pairs people read must stay legible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mornye import branding_tokens as bt

CSS = (
    Path(__file__).resolve().parents[1] / "mornye" / "frontend" / "styles.css"
).read_text(encoding="utf-8")


def _block(selector_regex: str) -> str:
    match = re.search(selector_regex + r"\s*\{(.*?)\n    \}", CSS, re.S)
    assert match, f"no CSS block for {selector_regex}"
    return match.group(1)


def _vars(block: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2).strip().lower()
        for m in re.finditer(r"--([\w-]+):\s*([^;]+);", block)
    }


ROOT = _vars(_block(r"\n    :root"))
LIGHT = {**ROOT, **_vars(_block(r"html\.light-theme,\s*body\.light-theme"))}


@pytest.mark.parametrize(
    ("mode", "tokens", "css"), [("dark", bt.DARK, ROOT), ("light", bt.LIGHT, LIGHT)]
)
def test_stylesheet_matches_the_shared_tokens(
    mode: str, tokens: dict, css: dict
) -> None:
    drift = {
        name: (value, css.get(name))
        for name, value in tokens.items()
        if css.get(name) != value.lower()
    }
    assert not drift, f"{mode}: token -> (branding_tokens, styles.css) differ: {drift}"


@pytest.mark.parametrize(("mode", "tokens"), [("dark", bt.DARK), ("light", bt.LIGHT)])
def test_text_pairs_meet_wcag_aa(mode: str, tokens: dict) -> None:
    weak = {
        f"{fg} on {bg}": round(bt.contrast_ratio(tokens[fg], tokens[bg]), 2)
        for fg, bg in bt.CONTRAST_PAIRS
        if bt.contrast_ratio(tokens[fg], tokens[bg]) < 4.5
    }
    assert not weak, f"{mode}: below 4.5:1 — {weak}"


def test_teal_is_the_default_accent_everywhere() -> None:
    from mornye.app import MornyeAPI

    app_js = (
        Path(__file__).resolve().parents[1] / "mornye" / "frontend" / "app.js"
    ).read_text(encoding="utf-8")
    js_accents = re.search(r"const ACCENTS = \[(.*?)\];", app_js).group(1)
    assert [a.strip(" '") for a in js_accents.split(",")] == list(MornyeAPI.ACCENTS)
    assert MornyeAPI.ACCENTS[0] == "teal"
    assert ROOT["accent"] == bt.DARK["accent"]
    # Every non-default accent has a dark and a light preset.
    for accent in MornyeAPI.ACCENTS[1:]:
        assert f'html[data-accent="{accent}"]' in CSS
        assert f'html.light-theme[data-accent="{accent}"]' in CSS


def test_textual_themes_build_from_the_tokens() -> None:
    from textual.theme import Theme

    for mode in ("dark", "light"):
        theme = Theme(**bt.textual_theme_kwargs(mode))
        source = bt.DARK if mode == "dark" else bt.LIGHT
        assert theme.name == f"mornye-{mode}"
        assert theme.primary.lower() == source["accent"]
        assert theme.background.lower() == source["bg"]
