"""The listening record (core/listening.py) and the personal Home
(core/personal_home.py), ported from Mornye mobile 5.4.5."""

from __future__ import annotations

from datetime import datetime, timedelta

from mornye.core import db, listening, personal_home as ph


def _lib(n: int, **extra) -> list[dict]:
    rows = []
    for i in range(n):
        artist = f"Artist {i % 13}"
        rows.append(
            {
                "path": f"/m/{artist}/Album {i % 9}/{i:03}.flac",
                "title": f"Song {i}",
                "artist": artist,
                "album": f"Album {i % 9}",
                "album_artist": artist,
                "track": i // 9 + 1,
                "disc": 1,
                "genre": ["Rock", "Pop", "Jazz"][i % 3],
                "year": str(1990 + i % 30),
                "added": 1_000_000 + i,
                **extra,
            }
        )
    return rows


def _store_library(rows: list[dict]) -> None:
    from mornye.core import player_library

    player_library.store([(r["path"], 1, 1, r) for r in rows])


# ── listening ───────────────────────────────────────────────────────────────


def test_track_key_folds_case_accents_and_features():
    assert (
        listening.track_key("Café Song", "Beyoncé feat. Jay-Z") == "cafe song|beyonce"
    )
    assert listening.track_key("", "x") == ""


def test_parse_quality_reads_depth_and_rate():
    assert listening.parse_quality("FLAC 24-bit / 96 kHz") == (24, 96000)
    assert listening.parse_quality("MP3 44.1 kHz 320 kbps") == (0, 44100)
    assert listening.parse_quality("") == (0, 0)


def test_record_adds_time_and_plays_per_day():
    at = datetime(2026, 11, 20, 21, 0).timestamp()
    assert listening.record("", {"title": "A", "artist": "B", "ms": 20000}, at=at)
    assert listening.record(
        "",
        {
            "title": "A",
            "artist": "B",
            "ms": 15000,
            "play": True,
            "quality": "FLAC 24-bit / 96 kHz",
        },
        at=at,
    )
    assert not listening.record("", {"title": "", "artist": "B", "ms": 1}, at=at)
    rows = listening.rows("")
    assert len(rows) == 1
    assert rows[0]["milliseconds"] == 35000 and rows[0]["plays"] == 1
    assert rows[0]["bit_depth"] == 24 and rows[0]["sample_rate"] == 96000
    assert rows[0]["day"] == "2026-11-20"


def test_record_is_per_owner_and_sessions_keep_the_hour():
    at = datetime(2026, 11, 20, 23, 30).timestamp()
    listening.record(
        "alice", {"title": "A", "artist": "B", "outcome": "listened"}, at=at
    )
    listening.record("bob", {"title": "A", "artist": "B", "ms": 1000}, at=at)
    assert listening.sessions("alice") == [
        {"at": at, "track_key": "a|b", "outcome": "listened", "hour": 23}
    ]
    assert listening.sessions("bob") == []
    assert listening.rows("alice") == []


def test_record_takes_genre_and_year_from_the_library():
    _store_library(
        [
            {
                "path": "/m/x.flac",
                "title": "X",
                "artist": "Y",
                "genre": "Jazz",
                "year": "1959",
                "format": "FLAC",
            }
        ]
    )
    listening.record(
        "",
        {"path": "/m/x.flac", "title": "X", "artist": "Y", "ms": 40000, "play": True},
    )
    row = listening.rows("")[0]
    assert (row["genre"], row["year"], row["format"]) == ("Jazz", 1959, "FLAC")


# ── layout ──────────────────────────────────────────────────────────────────


def test_default_layout_hides_finish_your_collection_and_on_repeat():
    layout = ph.read_layout({})
    assert ph.layout_sections(layout) == list(ph.SECTIONS)
    assert not ph.layout_shows(layout, "unfinished")
    assert not ph.layout_shows(layout, "onRepeat")
    assert ph.layout_shows(layout, "mixes")


def test_layout_keeps_new_sections_after_the_one_they_follow():
    order = ph.layout_sections({"order": ["mixes", "quickPlay", "topPicks"]})
    # Each missing section lands right after the one it follows when
    # shipped: recentlyAdded after mixes, pins after quickPlay…
    assert order == [
        "mixes",
        "recentlyAdded",
        "unfinished",
        "recommended",
        "onRepeat",
        "quickPlay",
        "pins",
        "topPicks",
        "recentlyPlayed",
    ]


def test_layout_with_turns_sections_on_and_off_and_persists():
    layout = ph.layout_with(
        {}, ["onRepeat", "mixes"], {"onRepeat": True, "mixes": False, "bogus": True}
    )
    assert ph.layout_shows(layout, "onRepeat")
    assert not ph.layout_shows(layout, "mixes")
    ph.save_layout("u", layout)
    assert ph.load_layout("u") == layout
    assert ph.load_layout("other") == ph.read_layout({})


# ── pins ────────────────────────────────────────────────────────────────────


def test_pins_limit_repeat_and_order():
    for i in range(ph.PIN_LIMIT):
        ok, _ = ph.add_pin("", {"kind": "artist", "id": f"a{i}", "title": f"A{i}"})
        assert ok
    ok, pins = ph.add_pin("", {"kind": "artist", "id": "a0", "title": "again"})
    assert ok and len(pins) == ph.PIN_LIMIT
    ok, _ = ph.add_pin("", {"kind": "genre", "id": "rock", "title": "Rock"})
    assert not ok
    pins = ph.move_pin("", "artist:a5", 0)
    assert pins[0]["id"] == "a5"
    pins = ph.remove_pin("", "artist:a5")
    assert len(pins) == ph.PIN_LIMIT - 1


def test_decode_pins_drops_unknown_and_repeats():
    pins = ph.decode_pins(
        [
            {"kind": "album", "id": "x /// y", "title": "Y"},
            {"kind": "album", "id": "x /// y", "title": "Y again"},
            {"kind": "loved", "id": "loved", "title": "Loved"},
            {"kind": "folder", "id": "", "title": "no id"},
            "junk",
        ]
    )
    assert [ph.pin_key(p) for p in pins] == ["album:x /// y"]


def test_pin_songs_for_each_kind():
    lib = _lib(18)
    album = ph.pin_songs("", {"kind": "album", "id": ph.album_key(lib[0])}, lib)
    assert album and all(s["album"] == "Album 0" for s in album)
    genre = ph.pin_songs("", {"kind": "genre", "id": "jazz"}, lib)
    assert genre and all(s["genre"] == "Jazz" for s in genre)
    folder = ph.pin_songs("", {"kind": "folder", "id": "/m/Artist 1"}, lib)
    assert folder and all(s["path"].startswith("/m/Artist 1/") for s in folder)
    artist = ph.pin_songs("", {"kind": "artist", "id": "artist 2"}, lib)
    assert artist and all(s["artist"] == "Artist 2" for s in artist)


# ── quick play ──────────────────────────────────────────────────────────────


def test_quick_play_is_nine_played_first_then_one_per_album():
    lib = _lib(60)
    played = [lib[5], lib[7]]
    picks = ph.quick_play(lib, played, "2026-11-26")
    assert len(picks) == 9
    assert picks[:2] == played
    albums = [ph.album_key(p) for p in picks]
    assert len(set(albums)) == len(albums)
    # Same day, same picks; another day, others.
    assert ph.quick_play(lib, played, "2026-11-26") == picks
    assert ph.quick_play(lib, played, "2026-11-27") != picks


# ── mixes ───────────────────────────────────────────────────────────────────


def test_no_mixes_for_a_small_library():
    assert ph.build_mixes(_lib(20), {}) == []


def test_mixes_include_deep_cuts_and_year():
    now = datetime(2026, 11, 26, 12)
    lib = _lib(240)
    for r in lib[:70]:
        r["year"] = "2026"
    plays: dict[str, float] = {}
    for r in lib:
        if (
            r["artist"] in {f"Artist {a}" for a in range(6)}
            and int(r["title"].split()[1]) % 4 == 0
        ):
            plays[listening.track_key(r["title"], r["artist"])] = 4
    mixes = ph.build_mixes(lib, plays, {}, now)
    kinds = [m["kind"] for m in mixes]
    assert len(mixes) <= ph.MIX_MAX
    assert "deepCuts" in kinds
    assert any(m["title"] == "2026 Mix" for m in mixes)
    for m in mixes:
        assert ph.MIX_MIN_SONGS <= len(m["songs"]) <= ph.MIX_TARGET
        assert len({s["path"] for s in m["songs"]}) == len(m["songs"])
    deep = next(m for m in mixes if m["kind"] == "deepCuts")
    assert all(
        listening.track_key(s["title"], s["artist"]) not in plays for s in deep["songs"]
    )
    # Same day, same mixes.
    assert ph.build_mixes(lib, plays, {}, now) == mixes


def test_artist_mix_rotates_daily_among_the_top_artists():
    lib = _lib(240)
    plays = {listening.track_key(r["title"], r["artist"]): 1 for r in lib[:60]}
    seen = set()
    for day in range(8):
        mixer = ph._Mixes(lib, plays, {}, datetime(2026, 11, 1 + day))
        seen.add(mixer.mix_artists(sparse=False)[0])
    assert len(seen) > 1


def test_order_recommendations_spreads_artists():
    tracks = [{"title": f"t{i}", "artist": "A"} for i in range(5)] + [
        {"title": "u", "artist": "B"}
    ]
    ordered = ph.order_recommendations(tracks, per_artist=3)
    assert [t["artist"] for t in ordered] == ["A", "A", "A", "B", "A", "A"]


# ── recommended ─────────────────────────────────────────────────────────────


def _fake_deezer(calls):
    def get(url, params):
        calls.append(url)
        if url.endswith("/search/artist"):
            return {"data": [{"id": len(params["q"]), "name": params["q"]}]}
        if url.endswith("/related"):
            return {"data": [{"id": 7, "name": "Other"}, {"id": 8, "name": "Artist 0"}]}
        if url.endswith("/7/top"):
            return {
                "data": [
                    {
                        "title": "Song 1",
                        "artist": {"name": "Artist 1"},
                        "album": {"title": "x"},
                    },
                    {
                        "title": "Fresh",
                        "artist": {"name": "Other"},
                        "album": {"title": "New", "cover_medium": "c"},
                        "link": "https://www.deezer.com/track/1",
                        "duration": 200,
                    },
                ]
            }
        return {"data": []}

    return get


def test_recommended_is_outside_the_library_and_cached_for_the_day():
    lib = _lib(40)
    now = datetime(2026, 11, 26, 9)
    calls: list[str] = []
    first = ph.recommended("", lib, now, fetch=_fake_deezer(calls))
    assert [t["title"] for t in first] == ["Fresh"]
    assert first[0]["url"] == "https://www.deezer.com/track/1"
    asked = len(calls)
    again = ph.recommended("", lib, now + timedelta(hours=3), fetch=_fake_deezer(calls))
    assert again == first and len(calls) == asked
    ph.recommended("", lib, now + timedelta(days=1), fetch=_fake_deezer(calls))
    assert len(calls) > asked


def test_recommended_keeps_yesterday_when_the_request_fails():
    lib = _lib(40)
    now = datetime(2026, 11, 26, 9)
    ph.recommended("", lib, now, fetch=_fake_deezer([]))

    def broken(url, params):
        raise RuntimeError("offline")

    later = ph.recommended("", lib, now + timedelta(days=1), fetch=broken)
    assert [t["title"] for t in later] == ["Fresh"]


# ── unfinished, on repeat, status ───────────────────────────────────────────


def test_incomplete_albums_count_gaps():
    lib = [
        {"path": "/a/1", "album": "A", "artist": "X", "track": 1},
        {"path": "/a/3", "album": "A", "artist": "X", "track": 3},
        {"path": "/b/1", "album": "B", "artist": "X", "track": 1},
        {"path": "/b/2", "album": "B", "artist": "X", "track": 2},
    ]
    found = ph.incomplete_albums(lib)
    assert [(a["title"], a["present"], a["expected"]) for a in found] == [("A", 2, 3)]


def test_unfinished_lists_albums_missing_songs():
    lib = [
        {"path": "/a/1", "album": "A", "artist": "X", "track": 1},
        {"path": "/a/3", "album": "A", "artist": "X", "track": 3},
    ]
    out = ph.unfinished("", lib)
    assert [r["id"] for r in out["rows"]] == ["incompleteAlbums"]
    assert out["rows"][0]["text"] == "1 album is missing songs"


def test_on_repeat_falls_back_to_albums_then_uses_plays():
    lib = _lib(12)
    now = datetime(2026, 11, 26, 12)
    assert ph.on_repeat("", lib, now)["kind"] == "albums"
    listening.record(
        "",
        {"title": "Song 3", "artist": "Artist 3", "ms": 60000, "play": True},
        at=now.timestamp(),
    )
    out = ph.on_repeat("", lib, now)
    assert out["kind"] == "songs" and out["songs"][0]["title"] == "Song 3"


def test_status_counts_arrivals_since_the_last_visit():
    yesterday = datetime(2026, 11, 25, 20).timestamp()
    today = datetime(2026, 11, 26, 9)
    ph.visit_window("", yesterday, "s1")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO downloads (owner, title, artist, downloaded_at) VALUES ('', 'T', 'A', ?)",
            (yesterday + 3600,),
        )
    out = ph.status("", today, "s2")
    assert out == {"added": 1, "since_yesterday": True}
    # The same session holds the window.
    assert ph.status("", today + timedelta(hours=1), "s2") == out


def test_home_returns_sections_in_order():
    _store_library(_lib(60))
    out = ph.home("", now=datetime(2026, 11, 26, 9), session="x", recommend=False)
    assert out["order"][0] == "topPicks"
    assert "unfinished" not in out["order"]
    assert len(out["sections"]["quickPlay"]) == 9
    assert out["greeting"] in ("Good morning", "Rise and shine", "Fresh start")
    assert out["library_size"] == 60


def test_api_methods_are_exposed_to_the_web():
    from mornye.webapp import ALLOWED_METHODS

    for name in (
        "record_listening",
        "get_personal_home",
        "pin_to_home",
        "save_home_layout",
    ):
        assert name in ALLOWED_METHODS
