"""JavaScript extension URL metadata through the downloader's client contract."""

from __future__ import annotations

import asyncio

import pytest

from mornye.application.download_engine import _call_metadata_get_url
from mornye.core.errors import ErrorKind, MornyeError
from mornye.core.models import TrackMetadata
from mornye.extensions.provider import JSExtensionProvider

_URL = "https://soundcloud.com/artist/track"


def _track(track_id: str = "123456", name: str = "Track") -> dict:
    return {
        "id": track_id,
        "name": name,
        "artists": "Artist",
        "album_name": "Release",
        "album_artist": "Album Artist",
        "duration_ms": 123456,
        "cover_url": "https://img.test/track.jpg",
        "release_date": "2026-01-02",
        "track_number": 0,
        "disc_number": 1,
        "isrc": "USRC17607839",
        "label": "Label",
        "copyright": "Copyright",
        "genre": "Electronic",
        "composer": "Composer",
        "external_urls": _URL,
        "provider_id": "soundcloud",
        "item_type": "track",
    }


class _Provider(JSExtensionProvider):
    def __init__(self, response: dict):
        self.name = "ext:soundcloud"
        self._response = response

    def handle_url(self, url: str) -> dict:
        return self._response


def _resolve(response: dict):
    return asyncio.run(_call_metadata_get_url(_Provider(response), _URL))


def test_track_url_becomes_typed_track_metadata():
    collection_name, tracks, cover = _resolve(
        {"success": True, "type": "track", "track": _track()}
    )

    assert collection_name == "Track"
    assert cover == "https://img.test/track.jpg"
    assert len(tracks) == 1
    track = tracks[0]
    assert isinstance(track, TrackMetadata)
    assert (track.id, track.title, track.artists) == ("123456", "Track", "Artist")
    assert track.external_url == _URL
    assert track.extra_info["provider_id"] == "soundcloud"
    assert track.publisher == "Label"
    assert track.duration_ms == 123456


@pytest.mark.parametrize(
    ("kind", "track_album", "expected_album", "expected_number", "expected_total"),
    [("album", "", "Collection", 2, 2), ("playlist", "Release", "Release", 0, 0)],
)
def test_collection_url_preserves_collection_semantics(
    kind, track_album, expected_album, expected_number, expected_total
):
    raw_tracks = [
        dict(_track("first", "First"), album_name=track_album),
        dict(_track("second", "Second"), album_name=track_album),
    ]
    collection_name, tracks, cover = _resolve(
        {
            "success": True,
            "type": kind,
            kind: {
                "id": "collection-1",
                "name": "Collection",
                "artists": "Collection Artist",
                "images": "https://img.test/collection.jpg",
                "tracks": raw_tracks,
            },
        }
    )

    assert collection_name == "Collection"
    assert cover == "https://img.test/collection.jpg"
    assert [track.id for track in tracks] == ["first", "second"]
    assert tracks[1].album == expected_album
    assert tracks[1].track_number == expected_number
    assert tracks[1].total_tracks == expected_total


def test_artist_url_uses_top_tracks_not_albums():
    artist = {
        "id": "artist-1",
        "name": "Artist",
        "images": "https://img.test/artist.jpg",
        "top_tracks": [_track()],
        "albums": [{"id": "must-not-be-a-track"}],
    }

    collection_name, tracks, cover = _resolve(
        {"success": True, "type": "artist", "artist": artist}
    )

    assert collection_name == "Artist"
    assert cover == "https://img.test/artist.jpg"
    assert [track.id for track in tracks] == ["123456"]
    assert tracks[0].track_number == 0


def test_extension_url_error_keeps_not_found_semantics():
    with pytest.raises(MornyeError) as caught:
        _resolve({"success": False, "error": "Invalid SoundCloud URL"})

    assert caught.value.kind is ErrorKind.TRACK_NOT_FOUND
    assert "Invalid SoundCloud URL" in str(caught.value)
