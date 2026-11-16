"""N4: adjustable density in the GUI (comfortable / compact)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import mornye.app as app_module
from mornye.app import MornyeAPI
from mornye.webapp import ALLOWED_METHODS

FRONT = Path(__file__).resolve().parents[1] / "mornye" / "frontend"


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings_file = tmp_path / "gui-settings.json"
    monkeypatch.setattr(app_module, "cache_path", lambda *parts: settings_file)
    instance = MornyeAPI.__new__(MornyeAPI)
    instance.log = lambda *a, **k: None
    instance.settings_file = settings_file
    return instance


def test_save_density_merges_and_refuses_unknown_values(api):
    api.settings_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert api.save_density("compact")["ok"]
    assert json.loads(api.settings_file.read_text()) == {
        "theme": "dark",
        "density": "compact",
    }
    assert not api.save_density("tiny")["ok"]


def test_reachable_from_the_web_ui():
    assert "save_density" in ALLOWED_METHODS
    assert "'save_density'" in (FRONT / "web-shim.js").read_text(encoding="utf-8")


def test_applied_before_the_first_paint_and_on_change():
    assert "mornye-density" in (FRONT / "boot-head.js").read_text(encoding="utf-8")
    assert "data-density" in (FRONT / "boot-body.js").read_text(encoding="utf-8")
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="config-density" data-onchange="changeDensity()"' in html


def test_compact_tokens_stay_on_the_4px_grid():
    css = (FRONT / "css" / "layout.css").read_text(encoding="utf-8")
    block = re.search(r':root\[data-density="compact"\] \{(.*?)\}', css, re.S)
    assert block
    values = [int(v) for v in re.findall(r":\s*(\d+)px;", block.group(1))]
    assert values and all(v % 4 == 0 for v in values)


def test_large_density_is_offered_saved_and_zooms(api):
    assert api.save_density("large")["ok"]
    assert json.loads(api.settings_file.read_text())["density"] == "large"
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert '<option value="large">' in html
    # The CSS fallback when the window can't zoom natively.
    assert ':root[data-zoom="css"] body { zoom:' in (
        FRONT / "css" / "layout.css"
    ).read_text(encoding="utf-8")


def test_set_page_zoom_rejects_junk_and_needs_a_window(api):
    api._window = None
    assert not api.set_page_zoom("big")["ok"]
    assert not api.set_page_zoom(1.15)["ok"]
