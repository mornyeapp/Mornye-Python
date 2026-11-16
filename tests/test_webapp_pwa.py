"""Tests for the PWA installability additions (manifest.json, sw.js, and
the tags/registration script wired into index.html) — see sw.js's own
comment for the network-first design and why it's installability, not
offline-caching-of-live-data.
"""

from __future__ import annotations

import json

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
webapp = pytest.importorskip("mornye.webapp")

TestClient = fastapi_testclient.TestClient


def test_manifest_is_served_and_valid() -> None:
    with TestClient(webapp.create_app()) as client:
        resp = client.get("/manifest.json")
    assert resp.status_code == 200

    manifest = resp.json()
    assert manifest["name"] == "Mornye"
    assert manifest["display"] == "standalone"
    assert len(manifest["icons"]) >= 1
    for icon in manifest["icons"]:
        assert {"src", "sizes", "type"} <= icon.keys()
    assert {icon["src"] for icon in manifest["icons"]} == {
        "assets/icons/logo-192.png",
        "assets/icons/logo-512.png",
    }


def test_manifest_icons_exist_as_real_files() -> None:

    manifest = json.loads(
        (webapp.FRONTEND_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    for icon in manifest["icons"]:
        icon_path = webapp.FRONTEND_DIR / icon["src"]
        assert icon_path.is_file(), f"missing icon file: {icon_path}"
        if icon["type"] == "image/png":
            # Confirms it's a real raster image, not an accidental 0-byte
            # or HTML-error-page file at that path.
            assert icon_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_service_worker_is_served_with_javascript_content_type() -> None:
    with TestClient(webapp.create_app()) as client:
        resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "registerExtension" not in resp.text  # sanity: not the wrong file
    assert "self.addEventListener" in resp.text


def test_service_worker_never_intercepts_api_or_ws_paths() -> None:
    sw_source = (webapp.FRONTEND_DIR / "sw.js").read_text(encoding="utf-8")
    assert '"/api/"' in sw_source
    assert '"/ws"' in sw_source


def test_index_html_declares_the_manifest_and_theme_color() -> None:
    with TestClient(webapp.create_app()) as client:
        html = client.get("/").text
    assert 'rel="manifest"' in html
    assert 'name="theme-color"' in html
    assert 'src="assets/icons/logo-transparent.png"' in html


def test_index_html_registers_the_service_worker_gated_on_web_mode() -> None:
    with TestClient(webapp.create_app()) as client:
        html = client.get("/").text
    shim = (webapp.FRONTEND_DIR / "web-shim.js").read_text(encoding="utf-8")
    boot_tail = (webapp.FRONTEND_DIR / "boot-tail.js").read_text(encoding="utf-8")
    # web-shim.js (injected by webapp.py's index()) sets the flag ...
    assert "window.__MORNYE_WEB_MODE__ = true" in shim
    # ... and boot-tail.js registers the worker only when it is set.
    assert "__MORNYE_WEB_MODE__" in boot_tail
    assert "navigator.serviceWorker.register" in boot_tail
    # The shim must run first, so the registration sees `true` in web mode
    # instead of reading `undefined` from a stale evaluation order.
    assert html.index('src="/web-shim.js"') < html.index('src="boot-tail.js"')


def test_the_browser_chrome_is_painted_in_the_console_ground() -> None:
    """Voce 27: theme-color, the manifest and the native window open on the
    v2 ground, so nothing frames the page in the old warm black."""
    import json

    from mornye.branding_tokens import v2_tokens

    dark, light = v2_tokens("dark")["ground"], v2_tokens("light")["ground"]
    html = (webapp.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (webapp.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    # The pre-paint script left index.html for boot-head.js (F1).
    boot_head = (webapp.FRONTEND_DIR / "boot-head.js").read_text(encoding="utf-8")
    manifest = json.loads(
        (webapp.FRONTEND_DIR / "manifest.json").read_text(encoding="utf-8")
    )

    assert f'name="theme-color" content="{dark}"' in html
    for source in (boot_head, app_js):
        assert f"dark ? '{dark}' : '{light}'" in source
    assert manifest["theme_color"] == manifest["background_color"] == dark
