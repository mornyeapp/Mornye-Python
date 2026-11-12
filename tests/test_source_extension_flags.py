"""Links only an extension can read, and what its manifest asks (Jellyfin, J4).

A Jellyfin link (``http://nas:8096/web/#/details?id=…``) was handed to the
Spotify client, which could only fail. It now goes to the extension whose
urlHandler claims it, and its tracks remember that extension, so the
download honours ``skipBuiltInFallback`` (only that extension downloads the
track) and ``skipMetadataEnrichment`` (no tags from other services).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mornye.application import download_engine as DE
from mornye.core import metadata_fallback as MF
from mornye.core.models import TrackMetadata

JELLYFIN_URL = "http://nas:8096/web/#/details?id=0123456789abcdef0123456789abcdef"


def _track(**extra) -> TrackMetadata:
    return TrackMetadata(
        id="abc",
        title="Song",
        artists="Artist",
        album="Album",
        album_artist="Artist",
        extra_info=extra,
    )


def test_extension_tracks_remember_their_source_and_keep_their_ids() -> None:
    class Provider:
        def _call(self, method, *args):
            assert method == "handleUrl"
            return {
                "success": True,
                "type": "album",
                "album": {
                    "name": "Album",
                    "tracks": [{"id": "0123abcd", "name": "Song", "artists": "A"}],
                },
            }

        def close(self):
            pass

    name, tracks, _cover, meta = asyncio.run(
        MF.ExtensionUrlClient(
            "jellyfin", "jellyfin", provider_factory=lambda n: Provider()
        ).get_url_async(JELLYFIN_URL)
    )
    assert name == "Album" and meta == {"link_type": "album"}
    [track] = tracks
    assert track.id == "0123abcd"
    assert track.extra_info["source_extension"] == "jellyfin"


def test_only_links_without_a_built_in_client_go_to_their_extension(
    monkeypatch,
) -> None:
    monkeypatch.setattr(MF, "fallback_extension_for", lambda url, m=None: "jellyfin")
    assert DE._own_source_extension_for(JELLYFIN_URL) == "jellyfin"
    assert DE._own_source_extension_for("https://open.spotify.com/album/x") is None
    assert DE._own_source_extension_for("https://soundcloud.com/a/b") is None


class _P:
    def __init__(self, name):
        self.name = name


@pytest.fixture
def manifest(monkeypatch):
    holder: dict = {}
    monkeypatch.setattr(DE, "_installed_manifest", lambda name: holder)
    return holder


def test_a_track_with_no_source_is_untouched(manifest) -> None:
    manifest.update(skipBuiltInFallback=True, skipMetadataEnrichment=True)
    providers = [_P("ext:tidal-web")]
    opts = DE.DownloadOptions(output_dir="/tmp/out")
    assert DE._honour_source_extension(_track(), providers, opts) == (providers, opts)


def test_skip_built_in_fallback_keeps_only_the_source(manifest) -> None:
    manifest.update(skipBuiltInFallback=True)
    providers = [_P("ext:tidal-web"), _P("ext:jellyfin"), _P("ext:qobuz-web")]
    kept, opts = DE._honour_source_extension(
        _track(source_extension="jellyfin"),
        providers,
        DE.DownloadOptions(output_dir="/tmp/out"),
    )
    assert [p.name for p in kept] == ["ext:jellyfin"]
    assert opts.enrich_metadata is True


def test_the_source_is_built_when_it_is_not_among_the_services(
    manifest, monkeypatch
) -> None:
    manifest.update(skipBuiltInFallback=True)
    asked: list[str] = []

    def build(name, opts):
        asked.append(name)
        return [_P(name)]

    monkeypatch.setattr(DE, "_build_providers_for_name", build)
    kept, _ = DE._honour_source_extension(
        _track(source_extension="jellyfin"),
        [_P("ext:tidal-web")],
        DE.DownloadOptions(output_dir="/tmp/out"),
    )
    assert asked == ["ext:jellyfin"] and [p.name for p in kept] == ["ext:jellyfin"]


def test_without_the_flag_the_other_services_stay(manifest) -> None:
    providers = [_P("ext:tidal-web")]
    kept, _ = DE._honour_source_extension(
        _track(source_extension="jellyfin"),
        providers,
        DE.DownloadOptions(output_dir="/tmp/out"),
    )
    assert kept == providers


def test_skip_metadata_enrichment_turns_enrichment_off(manifest) -> None:
    manifest.update(skipMetadataEnrichment=True)
    opts = DE.DownloadOptions(output_dir="/tmp/out")
    assert opts.enrich_metadata is True
    _, changed = DE._honour_source_extension(
        _track(source_extension="jellyfin"), [], opts
    )
    assert changed.enrich_metadata is False
    assert opts.enrich_metadata is True  # the caller's options are not mutated


def test_every_download_goes_through_it(manifest, monkeypatch) -> None:
    manifest.update(skipBuiltInFallback=True, skipMetadataEnrichment=True)
    seen: dict = {}

    async def one_pass(metadata, output_dir, providers, opts, position, is_album):
        seen["providers"] = [p.name for p in providers]
        seen["enrich"] = opts.enrich_metadata
        return SimpleNamespace(success=False, skipped=False, file_path="")

    monkeypatch.setattr(DE, "_download_one_pass_async", one_pass)
    asyncio.run(
        DE.download_one_async(
            _track(source_extension="jellyfin"),
            "/tmp/out",
            [_P("ext:tidal-web"), _P("ext:jellyfin")],
            DE.DownloadOptions(output_dir="/tmp/out"),
        )
    )
    assert seen == {"providers": ["ext:jellyfin"], "enrich": False}
