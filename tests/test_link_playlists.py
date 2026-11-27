"""The Player's playlists over Mornye Link (core/connect/link_playlists.py):
the phone reads and edits the same playlists as the Player page, and hears
every change, whoever made it."""

from __future__ import annotations

import pytest

from mornye.core import player_playlists, subsonic
from mornye.core.connect.model import CommandError
from tests.test_link_connect import SERVER, bridge  # noqa: F401

ALPHA_ID = subsonic._id("so-", "/m/a.flac")


def _get(bridge, op, args=None):  # noqa: F811
    return bridge.op(op, "GET", dict(args or {}), "p1")


def _post(bridge, op, args=None):  # noqa: F811
    return bridge.op(op, "POST", dict(args or {}), "p1")


def _frames(bridge):  # noqa: F811
    return [m for _, m in bridge.frames if m["type"] == "playlists.changed"]


def _page_events(bridge):  # noqa: F811
    return [p for fn, p in bridge.page_events if fn == "app_player_playlists_changed"]


def _stored(name="Mix"):
    return player_playlists.create_playlist(
        name,
        "",
        "",
        [
            {"path": "/m/a.flac", "title": "Alpha", "artist": "Ann", "duration_s": 200},
            {
                "path": "remote:isrc:USAAA0000001",
                "title": "Gamma",
                "artist": "Gus",
                "isrc": "USAAA0000001",
                "source_url": "https://open.spotify.com/track/x",
            },
        ],
        "",
    )


def test_list_and_get_show_library_songs_by_song_id(bridge):  # noqa: F811
    stored = _stored()
    player_playlists.create_playlist("Hers", "", "", [], "alice")
    listed = _get(bridge, "playlists")["playlists"]
    assert [p["name"] for p in listed] == ["Mix"]  # this account's only
    summary = listed[0]
    assert summary["trackCount"] == 2 and summary["downloadedCount"] == 1
    assert summary["updatedMs"] == player_playlists.updated_ms(stored)
    assert summary["hasCover"] is False and summary["followsUrl"] == ""
    tracks = _get(bridge, "playlists/get", {"id": stored["id"]})["playlist"]["tracks"]
    alpha, gamma = tracks
    assert alpha["songId"] == ALPHA_ID and alpha["serverId"] == SERVER
    assert alpha["key"] == ALPHA_ID and alpha["downloaded"] is True
    assert gamma["downloaded"] is False and gamma["isrc"] == "USAAA0000001"
    assert gamma["key"] == "remote:isrc:USAAA0000001"
    assert gamma["sourceUrl"] == "https://open.spotify.com/track/x"
    with pytest.raises(CommandError) as err:
        _get(bridge, "playlists/get", {"id": "nope"})
    assert err.value.code == "not_found"


def test_a_phone_creates_a_playlist_from_track_refs(bridge):  # noqa: F811
    bridge.phones.append("p1")
    out = _post(
        bridge,
        "playlists/create",
        {
            "name": "Road trip",
            "tracks": [
                {"title": "Beta", "artist": "Bob"},
                {"title": "Not here", "artist": "Nobody", "isrc": "gbxyz1234567"},
            ],
        },
    )["playlist"]
    beta, missing = out["tracks"]
    assert beta["downloaded"] is True and beta["title"] == "Beta"
    assert missing["downloaded"] is False and missing["isrc"] == "GBXYZ1234567"
    stored = player_playlists.get_playlist(out["id"], "")
    assert stored["tracks"][0]["path"] == "/m/b.flac"
    assert _frames(bridge)[-1] == {
        "type": "playlists.changed",
        "ids": [out["id"]],
        "deleted": False,
    }
    assert _page_events(bridge)[-1]["ids"] == [out["id"]]
    with pytest.raises(CommandError):
        _post(bridge, "playlists/create", {"name": " "})


def test_update_reorders_by_key_and_refuses_a_stale_edit(bridge):  # noqa: F811
    stored = _stored()
    got = _get(bridge, "playlists/get", {"id": stored["id"]})["playlist"]
    keys = [t["key"] for t in got["tracks"]]
    out = _post(
        bridge,
        "playlists/update",
        {
            "id": stored["id"],
            "name": "Renamed",
            "baseUpdatedMs": got["updatedMs"],
            "tracks": [
                {"key": keys[1]},
                {"key": keys[0]},
                {"title": "Beta", "artist": "Bob"},
            ],
        },
    )["playlist"]
    assert out["name"] == "Renamed"
    assert [t["title"] for t in out["tracks"]] == ["Gamma", "Alpha", "Beta"]
    # The source link of a kept entry survives the round trip.
    assert out["tracks"][0]["sourceUrl"] == "https://open.spotify.com/track/x"
    with pytest.raises(CommandError) as err:
        _post(
            bridge,
            "playlists/update",
            {"id": stored["id"], "baseUpdatedMs": got["updatedMs"], "tracks": []},
        )
    assert err.value.code == "conflict"
    assert len(player_playlists.get_playlist(stored["id"], "")["tracks"]) == 3


def test_delete_and_cover(bridge):  # noqa: F811
    bridge.phones.append("p1")
    stored = _stored()
    assert _get(bridge, "playlists/cover", {"id": stored["id"]}) == {"cover": ""}
    assert _post(bridge, "playlists/delete", {"id": stored["id"]}) == {"ok": True}
    assert player_playlists.get_playlist(stored["id"], "") is None
    assert _frames(bridge)[-1]["deleted"] is True
    assert _page_events(bridge)[-1] == {"ids": [stored["id"]], "deleted": True}
    with pytest.raises(CommandError) as err:
        _post(bridge, "playlists/delete", {"id": stored["id"]})
    assert err.value.code == "not_found"


def test_a_change_made_on_the_page_reaches_the_phones_only(bridge):  # noqa: F811
    bridge.phones.append("p1")
    stored = _stored()
    player_playlists.remove_track(stored["id"], "", "/m/a.flac")
    assert [f["ids"] for f in _frames(bridge)][-1] == [stored["id"]]
    assert _page_events(bridge) == []  # the page made it: it knows
    player_playlists.create_playlist("Hers", "", "", [], "alice")
    assert len(_frames(bridge)) == 2  # another account's change is not sent


def test_unknown_playlist_ops_are_not_found(bridge):  # noqa: F811
    with pytest.raises(CommandError) as err:
        _post(bridge, "playlists/merge")
    assert err.value.code == "not_found"
