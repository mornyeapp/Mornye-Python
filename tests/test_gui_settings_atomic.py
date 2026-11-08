"""gui-settings.json: no torn reads, no settings wiped by save_theme/save_accent.

The desktop page calls load_settings() and save_theme()/save_accent() on
separate pywebview threads at startup. With a truncate-then-write save, a
reader could catch the file empty ("Failed to load settings: Expecting
value: line 1 column 1 (char 0)"), and a save_* that read it empty wrote
back only its own key — wiping every other setting.
"""

from __future__ import annotations

import json
import threading

import pytest

import mornye.app as app_module
from mornye.app import MornyeAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings_file = tmp_path / "gui-settings.json"
    monkeypatch.setattr(app_module, "cache_path", lambda *parts: settings_file)
    instance = MornyeAPI.__new__(MornyeAPI)
    instance.errors = []
    instance.log = lambda msg, level="info": (
        instance.errors.append(msg) if level == "error" else None
    )
    instance.settings_file = settings_file
    return instance


def _write(api, data):
    api.settings_file.write_text(json.dumps(data), encoding="utf-8")


def _read(api):
    return json.loads(api.settings_file.read_text(encoding="utf-8"))


def test_save_theme_and_accent_keep_other_settings(api):
    _write(api, {"download_path": "/tmp/x", "quality": "LOSSLESS"})
    assert api.save_theme("dark")["ok"]
    assert api.save_accent("blue")["ok"]
    assert _read(api) == {
        "download_path": "/tmp/x",
        "quality": "LOSSLESS",
        "theme": "dark",
        "accent": "blue",
        "brand": MornyeAPI.BRAND_VERSION,
    }


def test_save_theme_does_not_persist_environment_defaults(api, monkeypatch):
    monkeypatch.setenv("MORNYE_QUALITY", "HI_RES_LOSSLESS")
    _write(api, {"download_path": "/tmp/x"})
    api.save_theme("light")
    assert _read(api) == {"download_path": "/tmp/x", "theme": "light"}


def test_unreadable_file_is_backed_up_not_silently_lost(api):
    api.settings_file.write_text("{not json", encoding="utf-8")
    assert api.save_theme("dark")["ok"]
    backup = api.settings_file.with_name("gui-settings.json.corrupt")
    assert backup.read_text(encoding="utf-8") == "{not json"
    assert _read(api) == {"theme": "dark"}
    assert any("unreadable" in e for e in api.errors)


def test_concurrent_saves_and_loads_never_see_a_torn_file(api):
    _write(api, {"download_path": "/tmp/x", "quality": "LOSSLESS"})
    stop = threading.Event()
    seen_empty = []

    def writer():
        modes = ("auto", "light", "dark")
        accents = ("teal", "blue", "pink")
        i = 0
        while not stop.is_set():
            api.save_theme(modes[i % 3])
            api.save_accent(accents[i % 3])
            i += 1

    def reader():
        while not stop.is_set():
            if not api.load_settings().get("download_path"):
                seen_empty.append(True)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    stop_timer = threading.Timer(1.0, stop.set)
    stop_timer.start()
    for t in threads:
        t.join(timeout=10)
    stop_timer.cancel()

    assert not api.errors
    assert not seen_empty
    final = _read(api)
    assert final["download_path"] == "/tmp/x"
    assert final["quality"] == "LOSSLESS"
