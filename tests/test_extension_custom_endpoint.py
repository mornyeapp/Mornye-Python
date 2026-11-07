"""Custom download endpoints for tidal-web / qobuz-web.

The extensions mark their ``customEndpoint`` setting ``"grantsNetwork":
true``; the host in it is handed to _netguard.js so a self-hosted server on
the LAN works without opening the whole local network. The legacy
``--tidal-api`` / ``--qobuz-local-api`` options set that same setting for
the running process.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mornye.extensions import provider
from mornye.extensions.sandbox import network_grants

MANIFEST = {
    "settings": [
        {"key": "customEndpoint", "type": "string", "grantsNetwork": True},
        {
            "key": "mirror",
            "type": "string",
            "grantsNetwork": True,
            "default": "https://Mirror.Example.test/v1",
        },
        {
            "key": "flag",
            "type": "boolean",
            "grantsNetwork": True,
            "default": "http://x.test",
        },
        {"key": "other", "type": "string", "default": "https://ignored.example.test"},
    ]
}


def test_grants_come_from_grants_network_string_settings_only() -> None:
    assert network_grants(MANIFEST, {}) == ["mirror.example.test"]
    assert network_grants(
        MANIFEST,
        {
            "customEndpoint": "http://192.168.1.20:8000/",
            "mirror": "  ",
            "other": "https://x.test",
        },
    ) == ["192.168.1.20", "mirror.example.test"]
    assert network_grants(MANIFEST, {"customEndpoint": "http://[::1]:8000"})[0] == "::1"


@pytest.mark.parametrize(
    "value",
    ["ftp://files.example.test", "https://user:pw@example.test", "not a url", 42],
)
def test_values_that_are_not_plain_http_urls_grant_nothing(value) -> None:
    assert network_grants(MANIFEST, {"customEndpoint": value}) == [
        "mirror.example.test"
    ]


def test_a_manifest_without_settings_grants_nothing() -> None:
    assert network_grants({}, {}) == []
    assert network_grants({"settings": "nope"}, {}) == []


def test_legacy_options_set_and_clear_the_custom_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(provider, "_SETTING_OVERRIDES", {})
    provider.apply_legacy_endpoint_options(
        SimpleNamespace(
            tidal_custom_api=" http://127.0.0.1:5000 ", qobuz_local_api_url=None
        )
    )
    assert provider._SETTING_OVERRIDES["tidal-web"] == {
        "customEndpoint": "http://127.0.0.1:5000"
    }
    assert provider._SETTING_OVERRIDES["qobuz-web"] == {}

    provider.apply_legacy_endpoint_options(
        SimpleNamespace(
            tidal_custom_api="", qobuz_local_api_url="http://127.0.0.1:8000"
        )
    )
    assert provider._SETTING_OVERRIDES["tidal-web"] == {}
    assert provider._SETTING_OVERRIDES["qobuz-web"] == {
        "customEndpoint": "http://127.0.0.1:8000"
    }
