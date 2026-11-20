from mornye.core.artist_overview import (
    artist_id_from_url,
    normalize_overview,
    uri_to_url,
)

AID = "4iHNK0tOyZPYnBU7nGAgpQ"


def _src(url, w):
    return {"url": url, "width": w, "height": w}


ARTIST = {
    "discography": {
        "topTracks": {
            "items": [
                {
                    "track": {
                        "id": "t1",
                        "uri": "spotify:track:t1",
                        "name": "Song",
                        "playcount": "1234",
                        "duration": {"totalMilliseconds": 241000},
                        "contentRating": {"label": "EXPLICIT"},
                        "albumOfTrack": {
                            "uri": "spotify:album:a1",
                            "coverArt": {
                                "sources": [_src("small", 64), _src("big", 640)]
                            },
                        },
                        "artists": {"items": [{"profile": {"name": "Mariah Carey"}}]},
                    }
                },
                {"track": None},
            ]
        },
        "albums": {
            "items": [
                {
                    "releases": {
                        "items": [
                            {
                                "uri": "spotify:album:a1",
                                "name": "Glitter",
                                "type": "ALBUM",
                                "date": {"year": 2001},
                                "tracks": {"totalCount": 11},
                            }
                        ]
                    }
                }
            ]
        },
        "singles": None,
        "popularReleasesAlbums": {
            "items": [{"uri": "spotify:album:a2", "name": "Merry", "type": "ALBUM"}]
        },
    },
    "relatedContent": {
        "relatedArtists": {
            "items": [{"uri": "spotify:artist:r1", "profile": {"name": "Whitney"}}]
        },
        "discoveredOnV2": {
            "items": [
                {"data": {"__typename": "GenericError"}},
                {
                    "data": {
                        "__typename": "Playlist",
                        "uri": "spotify:playlist:p1",
                        "name": "Hits",
                        "description": "<a href='x'>Best</a> songs",
                    }
                },
            ]
        },
        "appearsOn": {
            "items": [
                {
                    "releases": {
                        "items": [
                            {
                                "uri": "spotify:album:a3",
                                "name": "Duet",
                                "artists": {
                                    "items": [{"profile": {"name": "Westlife"}}]
                                },
                            }
                        ]
                    }
                }
            ]
        },
    },
    "profile": {
        "pinnedItem": {
            "comment": "Glitter 25",
            "itemV2": {
                "data": {
                    "__typename": "Album",
                    "uri": "spotify:album:a1",
                    "name": "Glitter",
                }
            },
        }
    },
}


def test_artist_id_and_uri():
    assert artist_id_from_url(f"https://open.spotify.com/artist/{AID}?si=x") == AID
    assert artist_id_from_url(f"spotify:artist:{AID}") == AID
    assert artist_id_from_url("https://open.spotify.com/album/x") == ""
    assert uri_to_url("spotify:album:a1") == "https://open.spotify.com/album/a1"
    assert uri_to_url("nonsense") == ""


def test_normalize_overview_sections():
    o = normalize_overview(ARTIST)
    assert o["top_tracks"] == [
        {
            "id": "t1",
            "name": "Song",
            "url": "https://open.spotify.com/track/t1",
            "album_url": "https://open.spotify.com/album/a1",
            "cover": "big",
            "artists": ["Mariah Carey"],
            "plays": 1234,
            "duration_ms": 241000,
            "explicit": True,
        }
    ]
    assert o["discography"]["albums"][0]["name"] == "Glitter"
    assert o["discography"]["albums"][0]["tracks"] == 11
    assert o["discography"]["singles"] == []
    assert o["discography"]["popular"][0]["url"] == "https://open.spotify.com/album/a2"
    # Error nodes are dropped; descriptions lose their markup.
    assert [p["description"] for p in o["discovered_on"]] == ["Best songs"]
    assert o["related_artists"][0]["name"] == "Whitney"
    assert o["appears_on"][0]["artists"] == ["Westlife"]
    assert o["pick"]["comment"] == "Glitter 25"
    assert o["featuring"] == [] and o["playlists"] == []


def test_normalize_overview_empty():
    o = normalize_overview({})
    assert o["top_tracks"] == [] and o["pick"] is None
    assert all(v == [] for v in o["discography"].values())


# ── load_track_for_queue ──────────────────────────────────────────────────


def test_load_track_for_queue_keeps_a_one_track_list(monkeypatch):
    """A Popular song from another artist's record is fetched by its link and
    kept as a tracklist of its own, which the Queue then downloads at index 0."""
    from types import SimpleNamespace

    import mornye.core.metadata_fallback as fallback
    from mornye.app import MornyeAPI

    track = SimpleNamespace(
        id="t9",
        title="Feel This Moment",
        artists="Pitbull, Christina Aguilera",
        album="Meltdown",
    )

    async def fake_fetch(url, factory, **kw):
        assert url == "https://open.spotify.com/track/t9"
        return ("Feel This Moment", [track])

    monkeypatch.setattr(fallback, "get_url_with_fallback", fake_fetch)
    api = MornyeAPI()
    res = api.load_track_for_queue("https://open.spotify.com/track/t9")
    assert res["status"] == "ok"
    assert res["track"] == {
        "id": "t9",
        "title": "Feel This Moment",
        "artist": "Pitbull, Christina Aguilera",
        "album": "Meltdown",
    }
    tracks, url, playlist_name = api._tracklists[res["list_id"]]
    assert tracks == [track] and url.endswith("/track/t9") and playlist_name == ""


def test_load_track_for_queue_refuses_other_links():
    from mornye.app import MornyeAPI

    res = MornyeAPI().load_track_for_queue("https://open.spotify.com/album/x")
    assert res["status"] == "error"
