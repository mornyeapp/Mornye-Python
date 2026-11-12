"""The contract between the GUI's markup and its script.

``app.js`` finds its elements by ``id``, wires them through inline ``on*=``
handlers and looks some up by class. The redesign rewrites ``index.html``
and every stylesheet but must leave that contract alone (see
``docs/redesign-plan.md``, §3 and Fase 0 voce 3). These tests read the files
as text — no browser, no server — and fail the moment the markup loses
something the script still relies on.

``frontend_dom_contract.json`` freezes the ids the script names anywhere as
a string, including the ones it reaches indirectly (``$(item.input)``) and
that no regex can follow. When an id is retired on purpose, regenerate it::

    MORNYE_UPDATE_DOM_CONTRACT=1 pytest tests/test_frontend_dom_contract.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
INVENTORY = Path(__file__).with_name("frontend_dom_contract.json")
SCRIPTS = ("app.js", "toast-system.js", "web-shim.js")

# Looked up by app.js but no longer in the markup. Each lookup is guarded,
# so nothing breaks — the update just lands nowhere. Listed so the test
# stays strict about everything else; remove an entry once it is resolved.
KNOWN_ORPHANS = {
    "hc-summary",  # updateStatusSummary(): the label left the health card
}

# Names an inline handler may call without the script defining them.
_HANDLER_GLOBALS = {
    "alert",
    "confirm",
    "encodeURIComponent",
    "event",
    "if",
    "Number",
    "parseInt",
    "return",
    "setTimeout",
    "String",
}


def _js() -> str:
    return "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in SCRIPTS)


def _html() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


def _html_ids(html: str) -> set[str]:
    return set(re.findall(r"""\bid=["']([\w-]+)["']""", html))


def _script_made_ids(js: str) -> set[str]:
    """Ids the script creates itself, in markup strings or by assignment."""
    in_markup = re.findall(r"""\bid=\\?["']([\w-]+)\\?["']""", js)
    assigned = re.findall(r"""\.id\s*=\s*['"]([\w-]+)['"]""", js)
    return set(in_markup) | set(assigned)


def _looked_up_ids(js: str) -> set[str]:
    """Ids the script fetches by a literal: ``$('x')``, ``getElementById``,
    ``#x`` inside a ``querySelector`` selector."""
    ids = set(re.findall(r"""\$\(\s*['"]([\w-]+)['"]\s*\)""", js))
    ids |= set(re.findall(r"""getElementById\(\s*['"]([\w-]+)['"]\s*\)""", js))
    for selector in re.findall(r"""querySelector(?:All)?\(\s*['"]([^'"]+)['"]""", js):
        ids |= set(re.findall(r"#([\w-]+)", selector))
    return ids


def _quoted_markup_ids(js: str, html: str) -> set[str]:
    """Every id of the markup that the script spells out as a string."""
    quoted = set(re.findall(r"""['"`]([A-Za-z][\w-]*)['"`]""", js))
    return quoted & _html_ids(html)


def test_every_id_the_script_looks_up_exists() -> None:
    js = _js()
    available = _html_ids(_html()) | _script_made_ids(js)
    missing = _looked_up_ids(js) - available - KNOWN_ORPHANS
    assert not missing, (
        "app.js looks these up but neither index.html nor the script "
        f"creates them: {sorted(missing)}"
    )


def test_known_orphans_are_still_orphans() -> None:
    """An entry fixed in the markup must leave KNOWN_ORPHANS too."""
    js = _js()
    available = _html_ids(_html()) | _script_made_ids(js)
    assert not (KNOWN_ORPHANS & available)
    assert KNOWN_ORPHANS <= _looked_up_ids(js)


def test_the_frozen_id_inventory_is_still_in_the_markup() -> None:
    js, html = _js(), _html()
    current = _quoted_markup_ids(js, html)
    if os.environ.get("MORNYE_UPDATE_DOM_CONTRACT") == "1":
        INVENTORY.write_text(json.dumps(sorted(current), indent=1) + "\n")
        pytest.skip("inventory regenerated")
    frozen = set(json.loads(INVENTORY.read_text(encoding="utf-8")))
    lost = frozen - _html_ids(html) - _script_made_ids(js)
    assert not lost, (
        f"index.html lost ids that app.js depends on: {sorted(lost)}. "
        "If that is intended, regenerate the inventory (see module docstring)."
    )
    new = current - frozen
    assert not new, (
        f"New ids are wired to the script but not in the inventory: {sorted(new)}. "
        "Regenerate it (see module docstring)."
    )


def test_every_inline_handler_calls_a_defined_function() -> None:
    js = _js()
    called: set[str] = set()
    for body in re.findall(r"""\bon\w+="([^"]*)\"""", _html()):
        called |= set(re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", body))
    defined = set(
        re.findall(
            r"(?:function\s+|(?:const|let|var)\s+|window\.)([A-Za-z_$][\w$]*)\s*(?:=|\()",
            js,
        )
    )
    undefined = called - defined - _HANDLER_GLOBALS
    assert (
        not undefined
    ), f"Inline handlers call undefined functions: {sorted(undefined)}"


def test_every_class_the_script_selects_is_somewhere() -> None:
    """A class app.js queries must be in the markup or in markup it writes."""
    js, html = _js(), _html()
    selected: set[str] = set()
    for selector in re.findall(
        r"""(?:querySelector(?:All)?|closest|matches)\(\s*['"]([^'"]+)['"]""", js
    ):
        selected |= set(re.findall(r"\.([A-Za-z][\w-]*)", selector))
    in_html: set[str] = set()
    for value in re.findall(r"""\bclass=["']([^"']+)["']""", html):
        in_html |= set(value.split())
    absent = {
        name
        for name in selected - in_html
        if not re.search(r"[\s'\"`]" + re.escape(name) + r"[\s'\"`]", js)
    }
    assert not absent, f"app.js selects classes that nothing produces: {sorted(absent)}"
