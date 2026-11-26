"""Finding a TrackRef in the library (core/connect/resolver.py), in the
order of the Mornye app's desktop host (link_catalog_index.dart)."""

from __future__ import annotations

from mornye.core import subsonic
from mornye.core.connect.model import TrackRef
from mornye.core.connect.resolver import Catalog, received_files

SERVER = "server-1"


def _catalog() -> Catalog:
    rows = [
        (
            "/m/a.flac",
            1,
            {
                "title": "Alpha",
                "artist": "Ann",
                "album": "One",
                "duration_s": 200,
                "isrc": "US-AAA-11-00001",
            },
        ),
        (
            "/m/b.flac",
            1,
            {"title": "Beta (Remastered)", "artist": "Bob feat. Cy", "duration_s": 180},
        ),
        ("/m/b2.flac", 1, {"title": "Beta", "artist": "Bob", "duration_s": 300}),
        ("/m/c.flac", 1, {"title": "Gamma", "artist": "Cat", "duration_s": 0}),
    ]
    return Catalog(subsonic.build_index(rows), SERVER)


def _id(path: str) -> str:
    return subsonic._id("so-", path)


def test_song_id_needs_this_server_or_the_same_title():
    cat = _catalog()
    hit = cat.song_for_ref(
        TrackRef("whatever", songId=_id("/m/a.flac"), serverId=SERVER)
    )
    assert hit.path == "/m/a.flac"
    assert (
        cat.song_for_ref(
            TrackRef("whatever", songId=_id("/m/a.flac"), serverId="other")
        )
        is None
    )
    assert (
        cat.song_for_ref(TrackRef("Alpha", songId=_id("/m/a.flac"))).path == "/m/a.flac"
    )
    assert cat.song_for_ref(TrackRef("Other", songId=_id("/m/a.flac"))) is None


def test_isrc_then_match_key_then_loose_key_with_duration():
    cat = _catalog()
    assert (
        cat.song_for_ref(TrackRef("x", isrc="usaaa1100001", durationMs=1)).path
        == "/m/a.flac"
    )
    assert (
        cat.song_for_ref(TrackRef("Alpha", "Ann", durationMs=201_000)).path
        == "/m/a.flac"
    )
    assert cat.song_for_ref(TrackRef("Alpha", "Ann", durationMs=230_000)) is None
    # Loose: brackets and featured artists do not count; the closest duration wins.
    assert (
        cat.song_for_ref(TrackRef("beta", "Bob", durationMs=181_000)).path
        == "/m/b.flac"
    )
    assert (
        cat.song_for_ref(TrackRef("Beta", "Bob", durationMs=299_000)).path
        == "/m/b2.flac"
    )
    # Unknown duration is the fallback.
    assert (
        cat.song_for_ref(TrackRef("Gamma", "Cat", durationMs=5_000)).path == "/m/c.flac"
    )


def test_refs_out_carry_the_song_id_and_server():
    cat = _catalog()
    ref = cat.ref_for_item({"path": "/m/a.flac", "title": "ignored"})
    assert ref.songId == _id("/m/a.flac") and ref.serverId == SERVER
    assert (
        ref.isrc == "USAAA1100001"
        and ref.matchKey == "alpha|ann"
        and ref.durationMs == 200_000
    )
    outside = cat.ref_for_item({"path": "/x/song.mp3", "artist": "Z", "duration_s": 3})
    assert outside == TrackRef("song", "Z", durationMs=3000, matchKey="song|z")


def test_resolve_falls_back_to_uploads_then_missing(tmp_path):
    cat = _catalog()
    fid = "ab" * 16
    (tmp_path / f"{fid}.flac").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    received = received_files(tmp_path)
    assert list(received) == [fid]
    out = cat.resolve(
        [
            TrackRef("Alpha", "Ann"),
            TrackRef("New", "N", fileId=fid),
            TrackRef("Nope"),
            None,
        ],
        received,
    )
    assert out[0].path == "/m/a.flac" and out[0].song is not None
    assert out[1].path.endswith(f"{fid}.flac") and out[1].item()["title"] == "New"
    assert out[2] is None and out[3] is None
    assert received_files(tmp_path / "missing") == {}
