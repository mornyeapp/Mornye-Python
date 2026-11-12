"""The per-extension "Allow local network access" switch (Jellyfin, J1).

Jellyfin takes its server's address in a plain ``server_url`` setting that
is not marked ``grantsNetwork``, so _netguard.js refused a server on the LAN
and the only way round it was ``MORNYE_EXT_ALLOW_PRIVATE_NETWORK=1``, which
opens the whole local network to every extension. The switch opens only the
hosts typed into that one extension's settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mornye.api_mixins import extension_settings
from mornye.extensions import local_network, provider
from mornye.extensions.sandbox import network_grants

JELLYFIN = {
    "name": "jellyfin",
    "settings": [
        {"key": "server_url", "type": "string", "required": True},
        {"key": "username", "type": "string"},
        {"key": "password", "type": "string", "secret": True},
        {"key": "test_button", "type": "button", "action": "testConnection"},
    ],
}
SAVED = {
    "server_url": "http://192.168.1.20:8096",
    "username": "me",
    "password": "http://secret.example.test",
}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MORNYE_DATA_DIR", str(tmp_path))
    return tmp_path


def test_without_the_switch_an_unmarked_server_url_grants_nothing() -> None:
    assert network_grants(JELLYFIN, SAVED) == []


def test_with_the_switch_only_the_typed_server_is_granted() -> None:
    # Not the secret, even when it happens to look like a URL; not the
    # username, which is not a URL at all.
    assert network_grants(JELLYFIN, SAVED, local_network=True) == ["192.168.1.20"]


def test_the_switch_is_off_until_turned_on_and_persists(_data_dir) -> None:
    assert local_network.is_allowed("jellyfin") is False
    local_network.set_allowed("jellyfin", True)
    assert local_network.is_allowed("jellyfin") is True
    assert local_network.is_allowed("tidal-web") is False
    stored = json.loads((_data_dir / "extension_local_network.json").read_text())
    assert stored == {"jellyfin": True}

    local_network.set_allowed("jellyfin", False)
    assert local_network.is_allowed("jellyfin") is False


def test_an_unreadable_file_means_off(_data_dir) -> None:
    (_data_dir / "extension_local_network.json").write_text("{nope")
    assert local_network.is_allowed("jellyfin") is False
    (_data_dir / "extension_local_network.json").write_text('{"jellyfin": "yes"}')
    assert local_network.is_allowed("jellyfin") is False


def test_the_runtime_is_started_with_the_granted_host(monkeypatch) -> None:
    seen: dict = {}

    class FakeRuntime:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(provider, "JSRuntime", FakeRuntime)
    fake = SimpleNamespace(
        _ext=SimpleNamespace(name="jellyfin", manifest=JELLYFIN, index_js="x.js"),
        _settings=SAVED,
        _node_executable="node",
        _signed_session=None,
        _session_signed_fetch_handler=None,
        _stop_requested=lambda: False,
        _storage=None,
    )
    provider.JSExtensionProvider._create_runtime(fake)  # type: ignore[arg-type]
    assert seen["granted_hosts"] == []

    local_network.set_allowed("jellyfin", True)
    provider.JSExtensionProvider._create_runtime(fake)  # type: ignore[arg-type]
    assert seen["granted_hosts"] == ["192.168.1.20"]


def test_the_api_reports_and_changes_the_switch(monkeypatch) -> None:
    ext = SimpleNamespace(
        name="jellyfin",
        display_name="Jellyfin",
        version="1.1.1",
        runtime="javascript",
        types=["metadata_provider"],
        manifest=JELLYFIN,
        settings_schema=JELLYFIN["settings"],
        quality_options=[],
    )
    manager = SimpleNamespace(
        get_installed=lambda name: ext if name == "jellyfin" else None,
        load_settings=lambda name: dict(SAVED),
    )
    forgotten: list[str] = []
    monkeypatch.setattr(extension_settings, "_manager", lambda: manager)
    monkeypatch.setattr(extension_settings, "_forget_running", forgotten.append)
    api = extension_settings.ExtensionSettingsMixin()

    before = api.get_extension_settings("jellyfin")
    assert before["local_network"] is False
    assert before["local_network_hosts"] == ["192.168.1.20"]

    after = api.set_extension_local_network("jellyfin", True)
    assert after["ok"] is True and after["local_network"] is True
    assert forgotten == ["jellyfin"]  # restarted, so the grant takes effect
    assert "error" in api.set_extension_local_network("nope", True)


def test_the_web_frontend_can_reach_it_but_only_as_admin() -> None:
    webapp = pytest.importorskip("mornye.webapp")
    assert "set_extension_local_network" in webapp.ALLOWED_METHODS
    assert "set_extension_local_network" in webapp.EXTENSION_SETTINGS_ADMIN_METHODS
