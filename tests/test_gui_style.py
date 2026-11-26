"""Interface style: the console look or the music-app look."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import mornye.app as app_module
from mornye.app import MornyeAPI
from mornye.webapp import ALLOWED_METHODS

FRONT = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
APP_JS = (FRONT / "app.js").read_text(encoding="utf-8")


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings_file = tmp_path / "gui-settings.json"
    monkeypatch.setattr(app_module, "cache_path", lambda *parts: settings_file)
    instance = MornyeAPI.__new__(MornyeAPI)
    instance.log = lambda *a, **k: None
    instance.settings_file = settings_file
    return instance


def test_save_style_merges_and_refuses_unknown_values(api):
    api.settings_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert api.save_style("music")["ok"]
    assert json.loads(api.settings_file.read_text()) == {
        "theme": "dark",
        "style": "music",
    }
    assert not api.save_style("neon")["ok"]


def test_styles_match_between_python_and_js():
    js = re.search(r"const STYLES = \[(.*?)\];", APP_JS).group(1)
    assert [s.strip(" '") for s in js.split(",")] == list(MornyeAPI.STYLES)
    assert MornyeAPI.STYLES[0] == "console", "the console look stays the default"


def test_reachable_from_the_web_ui():
    assert "save_style" in ALLOWED_METHODS
    assert "'save_style'" in (FRONT / "web-shim.js").read_text(encoding="utf-8")


def test_applied_before_the_first_paint_and_on_change():
    head = (FRONT / "boot-head.js").read_text(encoding="utf-8")
    assert "mornye-style" in head
    assert "css/music-tokens.css" in head and "css/music.css" in head
    assert "data-style" in (FRONT / "boot-body.js").read_text(encoding="utf-8")
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="config-style" data-onchange="changeStyle()"' in html


def test_music_rules_are_scoped_to_the_music_style():
    """The console look must not change: every rule of music.css sits under
    html[data-style="music"], every block of music-tokens.css under a
    data-style="music" selector."""
    css = re.sub(
        r"/\*.*?\*/", "", (FRONT / "css" / "music.css").read_text("utf-8"), flags=re.S
    )
    css = re.sub(r"@media[^{]+\{", "", css)  # the rules inside still count
    # Commas inside :is(…) / :has(…) are not selector separators.
    top_level = r",(?![^()]*\))"
    selectors = [
        s.strip()
        for m in re.finditer(r"([^{}]+)\{", css)
        for s in re.split(top_level, m.group(1))
    ]
    assert selectors and all(
        s.startswith('html[data-style="music"]') for s in selectors
    )
    tokens = re.sub(
        r"/\*.*?\*/",
        "",
        (FRONT / "css" / "music-tokens.css").read_text("utf-8"),
        flags=re.S,
    )
    selectors = [
        s.strip()
        for m in re.finditer(r"([^{}]+)\{", tokens)
        for s in m.group(1).split(",")
    ]
    assert selectors and all('[data-style="music"]' in s for s in selectors)


def test_home_carries_the_feed_in_the_music_style():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="explore-feed"' in html and 'id="recent-wrap"' in html
    assert "function mountHomeFeed(" in APP_JS
    assert (
        "if (name === 'home') { ensureHomeFeed(); renderMusicRecentSongs(); }" in APP_JS
    )


def test_music_only_parts_are_hidden_in_the_console_look():
    """The mockup's extra parts (sidebar search, Home title row, Link/Search/
    CSV control, Fetch label, Recently Added) are .m-only: hidden by
    layout.css, shown by music.css."""
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert html.count('class="m-only') + html.count(" m-only ") >= 5
    assert ".m-only.m-only { display: none; }" in (
        FRONT / "css" / "layout.css"
    ).read_text("utf-8")
    for name in (
        "setSearchModeTo",
        "musicSearch",
        "placeQueueDock",
        "renderMusicRecentSongs",
        "tintPickCard",
    ):
        assert f"function {name}(" in APP_JS or f"async function {name}(" in APP_JS


def test_the_feed_defaults_to_apple_music():
    assert "const DEFAULT_EXPLORE_SOURCE = 'ext:apple-music';" in APP_JS
