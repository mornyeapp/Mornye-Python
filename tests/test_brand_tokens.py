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
LIGHT_BLOCK = _vars(_block(r"html\.light-theme,\s*body\.light-theme"))
LIGHT = {**ROOT, **LIGHT_BLOCK}


BRIDGE = _vars(
    re.search(r"\n    :root,\s*html\.light-theme,\s*body\.light-theme\s*\{(.*?)\n    \}", CSS, re.S).group(1)  # type: ignore[union-attr]
)
V2_NAMES = set(bt.V2_SEMANTIC["dark"]) | {
    *bt.V2_SPACE,
    *bt.V2_SIZE,
    *bt.V2_RADIUS,
    *bt.V2_TYPE,
    *bt.V2_ELEVATION,
    *bt.V2_MOTION,
}


def test_the_gui_takes_its_palette_from_token_v2() -> None:
    """Redesign Fase 3, voce 10: styles.css's own names alias token v2.

    branding_tokens.DARK/LIGHT now drive only the TUI (until Fase 4). In the
    GUI each of those names is either a v2 name itself (and gone from the
    old blocks, where it would shadow v2) or bridged to a v2 token.
    """
    for name in bt.DARK:
        if name in V2_NAMES:
            assert name not in ROOT and name not in LIGHT_BLOCK, name
            continue
        value = BRIDGE.get(name)
        assert value, f"--{name} is not bridged to token v2"
        ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
        assert ref and ref.group(1) in V2_NAMES, f"--{name}: {value}"


def test_every_bridged_value_is_a_v2_token_or_neutral() -> None:
    for name, value in BRIDGE.items():
        if value in ("none", "transparent"):
            continue
        ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
        assert ref and ref.group(1) in V2_NAMES, f"--{name}: {value}"


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
    assert "data-accent" not in CSS, "styles.css no longer carries presets"


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
    fixed = {**bt.FIXED, **bt.FIXED_CHANNELS}
    drift = {
        name: (value, ROOT.get(name))
        for name, value in fixed.items()
        if ROOT.get(name) != value.lower()
    }
    assert not drift, f"token -> (branding_tokens, styles.css :root) differ: {drift}"
    light_block = _vars(_block(r"html\.light-theme,\s*body\.light-theme"))
    assert not set(fixed) & set(light_block), "the light theme overrides a fixed token"


def test_declarations_use_tokens_not_raw_colours() -> None:
    """Every colour lives in a custom property (Fase 1, voce 6).

    Masks are exempt: their black is an alpha channel, not a colour.
    """
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    raw = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        for declaration in rule.group(2).split(";"):
            prop = declaration.split(":", 1)[0].strip()
            if ":" not in declaration or prop.startswith("--") or "mask" in prop:
                continue
            if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d", declaration):
                raw.append(" ".join(f"{rule.group(1)} {{{declaration}}}".split()))
    assert not raw, "colours outside the tokens:\n  " + "\n  ".join(raw)
