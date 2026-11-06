"""Mornye centred on the JS extensions (Mornye-Extension).

* a service is downloaded by its JS extension first, the Python one only
  as a fallback;
* every extension's own manifest `settings` can be read, validated, saved
  and reset — from the API the GUI and TUI share;
* a quality is always one of the ids the extension declares in
  `qualityOptions`, from the global tier or a per-service pick;
* Spotify search merges the built-in client with spotify-web.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from mornye.core.models import TrackMetadata
from mornye.extensions.manager import ExtensionManager, validate_settings
from mornye.extensions.quality_options import has_atmos, pick_quality_option

TIDAL_OPTIONS = [
    {"id": "DOLBY_ATMOS", "label": "Dolby Atmos", "kind": "spatial"},
    {"id": "HI_RES_LOSSLESS", "label": "HiRes FLAC", "kind": "lossless"},
    {"id": "LOSSLESS", "label": "Lossless", "kind": "lossless"},
    {"id": "HIGH", "label": "High", "kind": "lossy"},
    {"id": "LOW", "label": "Low", "kind": "lossy"},
]
AMAZON_OPTIONS = [
    {"id": "best", "label": "FLAC Best Available", "kind": "lossless"},
    {"id": "opus", "label": "Opus 320kbps", "kind": "lossy"},
    {"id": "eac3", "label": "Dolby Digital Plus", "kind": "spatial"},
    {"id": "ac4", "label": "Dolby Atmos (AC-4)", "kind": "spatial"},
]
DEEZER_OPTIONS = [{"id": "flac", "label": "FLAC / Hi-Res"}]
QOBUZ_OPTIONS = [
    {"id": "HI_RES_LOSSLESS", "label": "24-Bit Max", "kind": "lossless"},
    {"id": "HI_RES", "label": "24-Bit", "kind": "lossless"},
    {"id": "LOSSLESS", "label": "Lossless", "kind": "lossless"},
]

YT_SETTINGS = [
    {"key": "homeCountry", "type": "select", "default": "ID", "options": ["US", "ID"]},
    {"key": "poTokenProviderUrl", "type": "string", "default": ""},
    {"key": "manualGvsPoToken", "type": "string", "default": "", "secret": True},
    {
        "key": "clear_cache",
        "type": "button",
        "label": "Clear",
        "action": "forceCleanup",
    },
]


def _write_ext(
    root, name, *, runtime="javascript", types=("download_provider",), **extra
):
    directory = root / name
    directory.mkdir()
    manifest = {
        "name": name,
        "version": "1.0.0",
        "runtime": runtime,
        "type": list(types),
    }
    manifest.update(extra)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / ("entry.py" if runtime == "python" else "index.js")).write_text(
        "", encoding="utf-8"
    )
    return directory


# ---------------------------------------------------------------------------
# Provider order
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_providers(monkeypatch, tmp_path):
    _write_ext(tmp_path, "tidal-web")
    _write_ext(tmp_path, "tidal-py", runtime="python", entryPoint="entry.py")

    class _JS:
        def __init__(self, ext_id, **_):
            self.name = f"ext:{ext_id}"

    class _Py:
        def __new__(cls, name, **_):
            return SimpleNamespace(name=name)

    monkeypatch.setattr("mornye.extensions.provider.JSExtensionProvider", _JS)
    monkeypatch.setattr(
        "mornye.extensions.python_provider.PythonExtensionProvider", _Py
    )
    return SimpleNamespace(ext_dir=str(tmp_path), timeout_s=None)


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("tidal", ["ext:tidal-web", "tidal-py"]),
        ("ext:tidal-web", ["ext:tidal-web"]),
        ("tidal-py", ["tidal-py"]),
    ],
)
def test_the_js_extension_downloads_first(fake_providers, service, expected) -> None:
    from mornye.application.provider_factory import build_providers_for_name

    built = build_providers_for_name(service, fake_providers)
    assert [p.name for p in built] == expected


# ---------------------------------------------------------------------------
# Quality ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "requested", "override", "expected"),
    [
        (TIDAL_OPTIONS, "DOLBY_ATMOS", None, "DOLBY_ATMOS"),
        (TIDAL_OPTIONS, "HI_RES", None, "HI_RES_LOSSLESS"),
        (TIDAL_OPTIONS, "HI_RES_LOSSLESS", "low", "LOW"),
        (AMAZON_OPTIONS, "DOLBY_ATMOS", None, "ac4"),
        (AMAZON_OPTIONS, "HI_RES_LOSSLESS", None, "best"),
        (AMAZON_OPTIONS, "LOSSLESS", "opus", "opus"),
        (AMAZON_OPTIONS, "LOSSLESS", "not-declared", "best"),
        (DEEZER_OPTIONS, "FLAC", None, "flac"),
        (DEEZER_OPTIONS, "DOLBY_ATMOS", None, "flac"),
        (QOBUZ_OPTIONS, "27", None, "HI_RES_LOSSLESS"),
        (QOBUZ_OPTIONS, "6", None, "LOSSLESS"),
        ([], "27", None, "27"),
    ],
)
def test_a_quality_is_always_a_declared_id(options, requested, override, expected):
    assert pick_quality_option(options, requested, override) == expected


def test_atmos_is_atmos_not_any_surround_format() -> None:
    assert has_atmos({"qualityOptions": AMAZON_OPTIONS})
    assert has_atmos({"qualityOptions": TIDAL_OPTIONS})
    assert not has_atmos(
        {
            "qualityOptions": [
                {"id": "eac3", "label": "Dolby Digital Plus", "kind": "spatial"}
            ]
        }
    )


def test_js_providers_get_the_tier_and_the_users_pick() -> None:
    from mornye.application.download_engine import (
        _extension_quality_override,
        _provider_quality,
    )

    js = SimpleNamespace(name="ext:amazon", select_quality=lambda *a: None)
    py = SimpleNamespace(name="amazon-py")
    opts = SimpleNamespace(quality="27", extension_quality={"amazon": "ac4"})

    assert _provider_quality(js, opts) == "HI_RES_LOSSLESS"
    assert _provider_quality(py, opts) == "best"
    assert _extension_quality_override(js, opts) == "ac4"
    assert _extension_quality_override(py, opts) is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_are_validated_against_the_manifest(tmp_path) -> None:
    _write_ext(tmp_path, "ytmusic-spotiflac", settings=YT_SETTINGS)
    ext = ExtensionManager(
        ext_dir=tmp_path, auto_install_downloads=False
    ).get_installed("ytmusic-spotiflac")
    schema = ext.settings_schema
    assert [s["type"] for s in schema] == ["select", "string", "string", "button"]
    assert schema[0]["options"] == [
        {"value": "US", "label": "US"},
        {"value": "ID", "label": "ID"},
    ]

    assert validate_settings(schema, {"homeCountry": "US"}) == {"homeCountry": "US"}
    with pytest.raises(ValueError) as err:
        validate_settings(schema, {"homeCountry": "XX", "nope": 1, "clear_cache": 1})
    message = str(err.value)
    assert "must be one of" in message
    assert "unknown setting 'nope'" in message
    assert "is an action" in message


def test_saving_merges_and_reset_forgets(tmp_path) -> None:
    _write_ext(tmp_path, "ytmusic-spotiflac", settings=YT_SETTINGS)
    manager = ExtensionManager(ext_dir=tmp_path, auto_install_downloads=False)

    manager.update_settings("ytmusic-spotiflac", {"homeCountry": "US"})
    effective = manager.update_settings(
        "ytmusic-spotiflac", {"poTokenProviderUrl": "http://pot:4416"}
    )
    assert effective["homeCountry"] == "US"
    assert effective["poTokenProviderUrl"] == "http://pot:4416"

    assert manager.reset_settings("ytmusic-spotiflac")["homeCountry"] == "ID"
    assert manager.load_settings("ytmusic-spotiflac")["homeCountry"] == "ID"


def test_the_api_never_returns_a_secret_and_keeps_it_on_empty(
    tmp_path, monkeypatch
) -> None:
    from mornye.api_mixins import extension_settings

    _write_ext(tmp_path, "ytmusic-spotiflac", settings=YT_SETTINGS)
    _write_ext(tmp_path, "tidal-py", runtime="python", entryPoint="entry.py")
    monkeypatch.setattr(
        extension_settings,
        "_manager",
        lambda: ExtensionManager(ext_dir=tmp_path, auto_install_downloads=False),
    )
    restarted = []
    monkeypatch.setattr(extension_settings, "_forget_running", restarted.append)
    api = extension_settings.ExtensionSettingsMixin()

    saved = api.save_extension_settings(
        "ytmusic-spotiflac", {"manualGvsPoToken": "s3cret", "homeCountry": "US"}
    )
    assert saved["ok"] is True
    assert saved["values"]["manualGvsPoToken"] == ""
    assert saved["secrets_set"] == ["manualGvsPoToken"]
    assert restarted == ["ytmusic-spotiflac"]

    # An empty secret means "unchanged".
    api.save_extension_settings("ytmusic-spotiflac", {"manualGvsPoToken": ""})
    manager = ExtensionManager(ext_dir=tmp_path, auto_install_downloads=False)
    assert manager.load_settings("ytmusic-spotiflac")["manualGvsPoToken"] == "s3cret"

    listed = api.list_extension_settings()["extensions"]
    assert [e["name"] for e in listed] == ["ytmusic-spotiflac"]  # Python ones aren't

    assert "error" in api.save_extension_settings(
        "ytmusic-spotiflac", {"homeCountry": "XX"}
    )
    assert "error" in api.run_extension_setting_action(
        "ytmusic-spotiflac", "homeCountry"
    )


def test_the_settings_methods_are_reachable_but_writes_are_admin_only() -> None:
    from mornye.app import MornyeAPI
    from mornye.webapp import ALLOWED_METHODS, EXTENSION_SETTINGS_ADMIN_METHODS

    for name in (
        "list_extension_settings",
        "get_extension_settings",
        "save_extension_settings",
        "reset_extension_settings",
        "run_extension_setting_action",
    ):
        assert name in ALLOWED_METHODS
        assert hasattr(MornyeAPI, name)
    assert "list_extension_settings" not in EXTENSION_SETTINGS_ADMIN_METHODS
    assert "save_extension_settings" in EXTENSION_SETTINGS_ADMIN_METHODS
    assert "check_tidal_api" not in ALLOWED_METHODS
    assert "check_qobuz_api" not in ALLOWED_METHODS


def test_cli_sets_and_shows_settings(tmp_path, monkeypatch, capsys) -> None:
    from mornye import launcher

    _write_ext(
        tmp_path,
        "ytmusic-spotiflac",
        settings=YT_SETTINGS,
        qualityOptions=[{"id": "best"}],
    )
    monkeypatch.setattr(
        "mornye.extensions.manager.ExtensionManager",
        lambda *a, **k: ExtensionManager(
            ext_dir=tmp_path, auto_install_downloads=False
        ),
    )

    assert launcher._ext_settings_command(None, ["youtube.homeCountry=US"]) == 0
    assert launcher._ext_settings_command("ytmusic-spotiflac", []) == 0
    out = capsys.readouterr().out
    assert "homeCountry = 'US'" in out
    assert "best" in out
    assert launcher._ext_settings_command(None, ["youtube.homeCountry=XX"]) == 1
    assert launcher._ext_settings_command(None, ["bad"]) == 2
    assert launcher._parse_ext_quality(["amazon=ac4", "broken"]) == {"amazon": "ac4"}


# ---------------------------------------------------------------------------
# Spotify search, merged
# ---------------------------------------------------------------------------


def _track(**fields):
    base = {
        "id": "1",
        "title": "Song",
        "artists": "A",
        "album": "X",
        "album_artist": "A",
    }
    return TrackMetadata(**{**base, **fields})


def _merged_search(builtin_result, extension_items):
    from mornye.core import metadata_fallback

    search = object.__new__(metadata_fallback.MergedSpotifySearch)

    class _Builtin:
        async def search_async(self, query, limit=50):
            if isinstance(builtin_result, BaseException):
                raise builtin_result
            return builtin_result

    class _Provider:
        def _call(self, method, *args):
            assert method == "searchTracks"
            return {"tracks": extension_items}

        def close(self):
            pass

    search._builtin = _Builtin()
    search._manager = SimpleNamespace(
        list_installed=lambda: [
            SimpleNamespace(
                name="spotify-web",
                types=["metadata_provider"],
                manifest={"urlHandler": {"patterns": ["open.spotify.com"]}},
                url_patterns=["open.spotify.com"],
            )
        ]
    )
    search._provider_factory = lambda name: _Provider()
    return search


def test_spotify_search_merges_the_extension_into_the_built_in_results() -> None:
    builtin = {
        "tracks": [_track(id="a", title="One", plays="99")],
        "albums": ["album"],
        "artists": [],
        "playlists": [],
    }
    items = [
        {"id": "a", "name": "One", "artists": "A", "label": "Label", "isrc": "usx1"},
        {"id": "b", "name": "Two", "artists": "B"},
    ]
    result = asyncio.run(_merged_search(builtin, items).search_async("q", limit=10))
    tracks = result["tracks"]
    assert [t.id for t in tracks] == ["a", "b"]
    assert (tracks[0].plays, tracks[0].publisher, tracks[0].isrc) == (
        "99",
        "Label",
        "USX1",
    )
    assert result["albums"] == ["album"]


def test_spotify_search_survives_a_broken_built_in_client() -> None:
    result = asyncio.run(
        _merged_search(
            RuntimeError("token"), [{"id": "b", "name": "Two"}]
        ).search_async("q")
    )
    assert [t.id for t in result["tracks"]] == ["b"]
