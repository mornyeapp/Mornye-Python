"""Keyboard and screen-reader basics of the web GUI (F7 in docs/redesign-plan.md).

* no native ``confirm()``: pywebview draws it in the OS style, outside the
  window; ``askConfirm()`` opens the app's own dialog (``#confirm-modal``);
* every element that acts on a click and is not a control of its own
  (``div``/``span``/…) can be reached with Tab and pressed with Enter/Space:
  it carries ``tabindex="0"`` and ``data-activate`` (the keydown handler in
  app.js turns the key into its click). Dialog backdrops are exempt: Escape
  and the dialog's own buttons close them;
* every ``<img>`` has an ``alt`` (empty when the image only decorates).
"""

from __future__ import annotations

import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
INDEX = (FRONT / "index.html").read_text(encoding="utf-8")
APP = (FRONT / "app.js").read_text(encoding="utf-8")

_NATIVE = {"button", "a", "input", "select", "textarea", "label", "option", "summary"}
#: Clicks that dismiss or only stop propagation, never an action of their own.
_EXEMPT = ("api-modal-backdrop", 'id="queue-backdrop"', "event.stopPropagation()")


def _code_lines(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


def test_no_native_confirm():
    assert not re.search(r"(?<![\w.])confirm\(", _code_lines(APP))
    assert 'id="confirm-modal"' in INDEX
    assert "function askConfirm(" in APP


def test_clickable_non_controls_are_keyboard_reachable():
    offenders = []
    for name, text in (("index.html", INDEX), ("app.js", APP)):
        for m in re.finditer(r"<([a-z][a-z0-9]*)\b[^<>]*\bonclick=[^<>]*>", text):
            tag, markup = m.group(1), m.group(0)
            if tag in _NATIVE or any(x in markup for x in _EXEMPT):
                continue
            if 'tabindex="0"' not in markup or "data-activate" not in markup:
                offenders.append(f"{name}: {markup[:100]}")
    assert not offenders, "\n".join(offenders)


def test_cards_built_in_js_are_activatable():
    # Cards made with createElement and an .onclick get makeActivatable().
    for cls in ("recent-card", "explore-card", "artist-album-card", "fb-entry"):
        m = re.search(rf"(\w+)\.className = '{cls}';\n\s*makeActivatable\(\1\);", APP)
        assert m, cls


def test_images_have_alt():
    missing = [
        m.group(0)[:80]
        for text in (INDEX, _code_lines(APP))
        for m in re.finditer(r"<img\b[^<>]*>", text)
        if not re.search(r"\balt=", m.group(0))
    ]
    assert not missing, missing


def test_search_results_take_down_the_fetching_notice():
    # Plan §0, voce 21: the "fetching metadata…" notice onFetch() raises
    # for a search stayed up over the results; the results handler must
    # end the fetching state (which also dismisses the notice).
    start = APP.index("window.app_handle_provider_search_results = function")
    body = APP[start : APP.index("// ── Build data", start)]
    assert "setFetchingState('hide')" in body
