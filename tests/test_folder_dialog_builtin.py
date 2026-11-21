"""MORNYE_FOLDER_DIALOG=builtin: Choose Folder without a second window.

In a kiosk compositor such as cage only one window is on screen, so the
native GTK folder dialog opened behind the app, and the app, waiting on it,
stopped answering. With the variable set, the desktop app opens the picker
drawn in the page, the one the web build already uses.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mornye import app as app_module

FRONTEND = Path(app_module.__file__).parent / "frontend" / "app.js"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("builtin", True),
        ("BUILTIN", True),
        (" builtin ", True),
        ("", False),
        ("native", False),
        ("1", False),
    ],
)
def test_the_variable_decides(monkeypatch, value, expected):
    monkeypatch.setenv(app_module.FOLDER_DIALOG_ENV, value)
    assert app_module.folder_dialog_is_builtin() is expected


def _api(window):
    api = object.__new__(app_module.MornyeAPI)
    pushed: list[tuple] = []
    api._window = window
    api._push = lambda name, *args: pushed.append((name, *args))
    return api, pushed


def test_choose_folder_opens_the_page_picker_and_no_native_dialog(monkeypatch):
    monkeypatch.setenv(app_module.FOLDER_DIALOG_ENV, "builtin")

    def native(*_args, **_kwargs):
        raise AssertionError("the native dialog must not open")

    api, pushed = _api(SimpleNamespace(create_file_dialog=native))
    api.choose_folder()

    assert pushed == [("openDownloadFolderBrowser",)]


def test_without_the_variable_the_native_dialog_is_used(monkeypatch):
    monkeypatch.delenv(app_module.FOLDER_DIALOG_ENV, raising=False)
    opened: list = []
    api, pushed = _api(
        SimpleNamespace(create_file_dialog=lambda *a, **k: opened.append(a) or None)
    )
    api.choose_folder()

    assert opened, "the native dialog opens as before"
    assert pushed == []


def test_the_page_has_the_handler_and_ends_in_set_download_dir():
    source = FRONTEND.read_text(encoding="utf-8")
    assert (
        "window.openDownloadFolderBrowser = () => openFolderBrowser('download')"
        in source
    )
    start = source.index("function applyFolderBrowserChoice(path)")
    body = source[start : source.index("\n}\n", start)]
    assert "folderBrowserTarget === 'download'" in body
    assert "set_download_dir" in body
