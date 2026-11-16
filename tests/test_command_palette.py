"""N1: the GUI's command palette and shortcuts (frontend/palette.js)."""

from __future__ import annotations

import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
PALETTE = (FRONT / "palette.js").read_text(encoding="utf-8")
APP = (FRONT / "app.js").read_text(encoding="utf-8")


def test_loaded_after_app_js_and_cached_by_the_service_worker():
    boot = (FRONT / "boot-tail.js").read_text(encoding="utf-8")
    assert boot.index("app.js?_=") < boot.index("palette.js?_=")
    assert '"palette.js"' in (FRONT / "sw.js").read_text(encoding="utf-8")


def test_every_app_function_a_command_calls_exists():
    called = set(re.findall(r"window\.([A-Za-z_]\w*)\(", PALETTE))
    called -= {"toastMgr", "openCommandPalette"}
    missing = [
        name
        for name in sorted(called)
        if not re.search(rf"^(?:async )?function {name}\(", APP, re.M)
    ]
    assert not missing, missing


def test_every_settings_tab_command_names_a_real_tab():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    for tab in re.findall(r"settingsTab\('(\w+)'\)", PALETTE):
        assert f'id="tc-{tab}"' in html, tab


def test_shortcuts_stay_out_of_text_fields():
    # `/` and `?` must type normally in a field; Ctrl/Cmd+K works anywhere.
    assert "typing(e.target)" in PALETTE
    assert "e.ctrlKey || e.metaKey" in PALETTE
