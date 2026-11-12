"""index.html keeps its look in stylesheets, not in style="" attributes.

Fase 1 of docs/redesign-plan.md moved every inline declaration into a class
(the ``u-*`` utilities at the end of styles.css), so that a new stylesheet
can reach all of it. What is left inline is there on purpose:

* properties app.js clears at runtime (``el.style.display = ''``) — in a
  class, clearing them would bring the class value back instead of the
  default; voce 5 turns them into state classes;
* a few declarations an id or attribute rule in styles.css would otherwise
  override (``#album-card > *``, ``.s-row > input[type="text"]`` on phones),
  kept until Fase 3 rewrites those rules.

This test fails when a new inline style appears outside those cases.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "mornye" / "frontend" / "index.html"

#: Cleared by app.js somewhere, so they may stay inline on any element.
RUNTIME_PROPERTIES = {
    "display",
    "pointer-events",
    "background-image",
    "padding-bottom",
    "transform",
    "opacity",
    "visibility",
}

#: Element marker → properties a stylesheet rule would otherwise override.
_FIELD = {"flex", "min-width", "max-width"}
EXCEPTIONS: dict[str, set[str]] = {
    'onclick="closeAlbumCard()"': {"position"},
    'id="local-results-wrap"': {"flex"},
    'id="api-config-status"': {"color"},
    **{
        f'id="{element}"': _FIELD
        for element in (
            "player-path-input",
            "subscription-url-input",
            "ce-tidal",
            "ce-qobuz",
            "ce-qobuz-key",
            "registry-url-input",
            "directory-url-input",
            "trust-key-name-input",
            "trust-key-value-input",
        )
    },
}


def test_inline_styles_hold_only_what_must_stay_inline() -> None:
    html = INDEX.read_text(encoding="utf-8")
    offending = []
    for tag in re.findall(r"<[a-zA-Z][^<>]*\sstyle=\"[^\"]*\"[^<>]*>", html):
        style = re.search(r'\sstyle="([^"]*)"', tag).group(1)  # type: ignore[union-attr]
        allowed = set(RUNTIME_PROPERTIES)
        for marker, props in EXCEPTIONS.items():
            if marker in tag:
                allowed |= props
        for declaration in filter(str.strip, style.split(";")):
            prop = declaration.split(":", 1)[0].strip().lower()
            if prop not in allowed:
                offending.append(f"{prop} in {tag[:90]}")
    assert (
        not offending
    ), 'Put these in a class (styles.css) instead of style="":\n  ' + "\n  ".join(
        offending
    )


def test_every_utility_class_used_in_the_markup_exists() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = (INDEX.parent / "styles.css").read_text(encoding="utf-8")
    used = set(re.findall(r'class="[^"]*', html))
    names = {n for chunk in used for n in re.findall(r"\bu-[\w-]+", chunk)}
    defined = set(re.findall(r"^\.(u-[\w-]+)\.\1 \{", css, re.M))
    assert names <= defined, sorted(names - defined)
    assert defined <= names, f"unused utilities: {sorted(defined - names)}"
