"""mornye.desktop_branding: app icon (and macOS name) for from-source runs."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

from mornye import desktop_branding as db

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ICO_MAGIC = b"\x00\x00\x01\x00"


def test_icon_assets_ship_with_package():
    assert db.app_icon_path().read_bytes()[:8] == PNG_MAGIC
    assert db.windows_icon_path().read_bytes()[:4] == ICO_MAGIC


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "logo.ico"), ("linux", "logo.png"), ("freebsd14", "logo.png")],
)
def test_window_icon_for_webview_start(platform, expected):
    icon = db.window_icon_path(platform)
    assert icon is not None
    assert Path(icon).name == expected
    assert Path(icon).is_file()


def test_no_webview_icon_on_macos():
    # macOS gets its (rounded) icon from apply_macos_branding; pywebview's
    # icon= would replace it with the square one.
    assert db.window_icon_path("darwin") is None


def test_missing_icon_file_gives_none(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_ICONS_DIR", tmp_path)
    assert db.window_icon_path("linux") is None
    assert db.window_icon_path("win32") is None


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_macos_branding_noop_elsewhere(platform, monkeypatch):
    # Off macOS it must not even try to import PyObjC.
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"AppKit", "Foundation"}:
            raise AssertionError(f"imported {name} on {platform}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert db.apply_macos_branding(platform=platform) is False


def test_macos_branding_without_pyobjc(monkeypatch):
    # A darwin run with PyObjC missing must not raise: the app starts anyway.
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "Foundation", None)
    assert db.apply_macos_branding(platform="darwin") is False


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_windows_app_id_noop_elsewhere(platform):
    assert db.apply_windows_app_id(platform) is False


def test_windows_app_id_never_raises(monkeypatch):
    # Pretend to be Windows on a host without ctypes.windll: no exception.
    import ctypes

    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert db.apply_windows_app_id("win32") is False


def test_desktop_branding_returns_icon_and_never_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "Foundation", None)
    assert db.apply_desktop_branding("linux") == db.window_icon_path("linux")
    assert db.apply_desktop_branding("darwin") is None


def test_applies_name_and_icon_on_macos(monkeypatch):
    # Fake PyObjC on purpose: the real NSApplication.sharedApplication()
    # would turn the pytest process into a Dock app that never finishes
    # launching, and its icon bounces until the run ends.
    import types

    info, localized = {}, {}
    applied_icons = []

    class Bundle:
        def infoDictionary(self):
            return info

        def localizedInfoDictionary(self):
            return localized

    class Image:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithContentsOfFile_(self, path):
            self.path = path
            return self

        def initWithSize_(self, size):
            raise RuntimeError("no drawing in tests")  # -> square fallback

    class App:
        def setApplicationIconImage_(self, image):
            applied_icons.append(image)

    appkit = types.SimpleNamespace(
        NSImage=Image,
        NSApplication=types.SimpleNamespace(sharedApplication=App),
    )
    foundation = types.SimpleNamespace(
        NSBundle=types.SimpleNamespace(mainBundle=Bundle)
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)

    assert db.apply_macos_branding(platform="darwin") is True
    assert info["CFBundleName"] == localized["CFBundleName"] == "Mornye"
    assert len(applied_icons) == 1
    assert applied_icons[0].path == str(db.app_icon_path())
