"""Token v2, the console look's design system (docs/redesign-plan.md, voce 8).

branding_tokens.py is the one source; frontend/css/tokens.css is generated
from it and the Textual themes are built from it. These tests keep the copy
exact, the two levels honest, and both themes legible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mornye import branding_tokens as bt

TOKENS_CSS = Path(bt.__file__).with_name("frontend") / "css" / "tokens.css"
HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_tokens_css_is_the_generated_copy() -> None:
    assert (
        TOKENS_CSS.read_text(encoding="utf-8") == bt.v2_tokens_css()
    ), "frontend/css/tokens.css is out of date: run `python -m mornye.branding_tokens`"


def test_primitives_are_plain_lowercase_hex() -> None:
    bad = {k: v for k, v in bt.V2_PRIMITIVES.items() if not HEX.match(v)}
    assert not bad


def test_both_themes_define_the_same_semantic_tokens() -> None:
    assert set(bt.V2_SEMANTIC["dark"]) == set(bt.V2_SEMANTIC["light"])


def test_semantic_tokens_point_at_primitives_only() -> None:
    for mode, mapping in bt.V2_SEMANTIC.items():
        missing = {k: v for k, v in mapping.items() if v not in bt.V2_PRIMITIVES}
        assert not missing, f"{mode}: {missing}"


def test_every_primitive_is_used() -> None:
    used = {ref for mapping in bt.V2_SEMANTIC.values() for ref in mapping.values()}
    assert set(bt.V2_PRIMITIVES) == used, sorted(set(bt.V2_PRIMITIVES) - used)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_contrast_pairs_hold(mode: str) -> None:
    t = bt.v2_tokens(mode)
    weak = {
        f"{fg} on {bg}": round(bt.contrast_ratio(t[fg], t[bg]), 2)
        for fg, bg, minimum in bt.V2_CONTRAST
        if bt.contrast_ratio(t[fg], t[bg]) < minimum
    }
    assert not weak, f"{mode}: below the required ratio: {weak}"


def test_one_signal_colour_per_theme() -> None:
    """The look's rule: a single signal. Its roles may differ in shade (for
    contrast) but all belong to the amber family."""
    for mode, mapping in bt.V2_SEMANTIC.items():
        roles = {
            mapping[k] for k in ("signal", "signal-strong", "signal-text", "signal-ink")
        }
        assert all(r.startswith("amber-") for r in roles), (mode, roles)


def test_space_sits_on_the_four_pixel_grid() -> None:
    sizes = {**bt.V2_SPACE, **bt.V2_SIZE}
    off = {k: v for k, v in sizes.items() if int(v.removesuffix("px")) % 4}
    assert not off


def test_motion_never_exceeds_120ms() -> None:
    for name, value in bt.V2_MOTION.items():
        if value.endswith("ms"):
            assert int(value.removesuffix("ms")) <= 120, name


def test_flat_elevation() -> None:
    """Separation is a rule, never a shadow with blur."""
    for name, value in bt.V2_ELEVATION.items():
        assert value == "none" or re.fullmatch(
            r"0 0 0 1px var\(--[\w-]+\)", value
        ), name


def test_one_font_family() -> None:
    assert bt.V2_TYPE["font"].startswith("'JetBrains Mono'")


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_textual_theme_builds(mode: str) -> None:
    from textual.theme import Theme

    theme = Theme(**bt.v2_textual_theme_kwargs(mode))
    t = bt.v2_tokens(mode)
    assert theme.primary.lower() == t["signal-strong"]
    assert theme.background.lower() == t["ground"]
    assert theme.dark is (mode == "dark")


#: Every stylesheet built on tokens.css: the component library and the
#: Fase 3 files that restyle the app (layout, legacy controls, views).
#: accents.css is left out with tokens.css: like it, it defines token values
#: (the --signal of each accent choice, voce 28) rather than using them.
#: music-tokens.css likewise: the token values of the music look (Settings →
#: Interface Style), which css/music.css builds on.
SHEETS = sorted(
    str(p.relative_to(TOKENS_CSS.parent))
    for p in TOKENS_CSS.parent.rglob("*.css")
    if p.name not in ("tokens.css", "accents.css", "music-tokens.css")
)


@pytest.mark.parametrize("sheet", SHEETS)
def test_components_use_semantic_tokens_only(sheet: str) -> None:
    """GUI and TUI consume the semantic level only (redesign-plan §3): no
    colour literal and no primitive in the stylesheets built on tokens.css."""
    css = re.sub(
        r"/\*.*?\*/",
        "",
        (TOKENS_CSS.parent / sheet).read_text(encoding="utf-8"),
        flags=re.S,
    )
    # rgba(var(--media-ink-rgb), .6) is a token's translucent shade
    # (branding_tokens.FIXED_CHANNELS), not a colour literal.
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?)\(\s*(?!var\()", css)
    primitives = [
        name
        for name in re.findall(r"var\(--([\w-]+)\)", css)
        if name in bt.V2_PRIMITIVES
    ]
    assert not literals, f"{sheet}: colour literals {literals}"
    assert not primitives, f"{sheet}: primitives used directly {primitives}"


@pytest.mark.parametrize("sheet", SHEETS)
def test_every_token_a_stylesheet_names_exists(sheet: str) -> None:
    css = (TOKENS_CSS.parent / sheet).read_text(encoding="utf-8")
    # tokens.css, the sheet's own, the shell's (layout.css: rail and dock
    # sizes, stacking order, which its state rules change), and those app.js
    # sets on an element (a stat bar's value, the album glow).
    defined = set(re.findall(r"--([\w-]+):", TOKENS_CSS.read_text(encoding="utf-8")))
    defined |= set(re.findall(r"--([\w-]+):", css))
    defined |= set(
        re.findall(
            r"--([\w-]+):",
            (TOKENS_CSS.parent / "layout.css").read_text(encoding="utf-8"),
        )
    )
    # The music look's own names (--m-*), defined with its token values.
    defined |= set(
        re.findall(
            r"--([\w-]+):",
            (TOKENS_CSS.parent / "music-tokens.css").read_text(encoding="utf-8"),
        )
    )
    script = (TOKENS_CSS.parent.parent / "app.js").read_text(encoding="utf-8")
    defined |= set(re.findall(r"setProperty\(\s*'--([\w-]+)'", script))
    defined |= set(re.findall(r"--([\w-]+):\$\{", script))
    used = set(re.findall(r"var\(--([\w-]+)", css))
    assert used <= defined, sorted(used - defined)
