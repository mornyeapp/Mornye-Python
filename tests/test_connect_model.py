"""Mornye Connect wire shapes (core/connect/model.py), checked against the
Mornye app's Link protocol (link_protocol.dart, link_player.dart)."""

from __future__ import annotations

import pytest

from mornye.core.connect.model import (
    CommandError,
    PlayerState,
    TrackRef,
    loose_key,
    match_key,
    normalize_isrc,
    queue_window,
    state_changed,
    validate_command,
    validate_handoff,
)


def _state(**kw) -> PlayerState:
    base = dict(
        track=TrackRef("A", "X"),
        playing=True,
        positionMs=10_000,
        durationMs=200_000,
        updatedMs=1_000_000,
        queue=(TrackRef("A", "X"), TrackRef("B", "Y"), TrackRef("C", "Z")),
        index=0,
    )
    base.update(kw)
    return PlayerState(**base)


def test_keys_match_the_app():
    assert match_key("  Song ", "Artist") == "song|artist"
    assert match_key("", "Artist") == ""
    assert loose_key("Song (Remastered 2011)", "Artist feat. Other") == "song|artist"
    assert loose_key("Hello, World!", "A & B") == "helloworld|a"
    assert normalize_isrc(" us-abc-12-00001 ") == "USABC1200001"


def test_trackref_round_trip_drops_bad_file_ids():
    ref = TrackRef.from_json(
        {
            "title": "T",
            "artist": "A",
            "durationMs": 1000.0,
            "fileId": "nothex",
            "songId": "so-1",
        }
    )
    assert ref == TrackRef("T", "A", durationMs=1000, songId="so-1")
    assert ref.to_json() == {
        "title": "T",
        "artist": "A",
        "durationMs": 1000,
        "songId": "so-1",
    }
    assert TrackRef.from_json({"artist": "no title"}) is None
    good = TrackRef.from_json({"title": "T", "fileId": "0" * 32})
    assert good.fileId == "0" * 32


def test_position_is_extrapolated_while_playing_and_capped():
    s = _state()
    assert s.position_at(1_005_000) == 15_000
    assert s.position_at(9_999_999) == 200_000
    assert _state(playing=False).position_at(1_005_000) == 10_000
    fresh = s.at(1_002_000)
    assert (fresh.positionMs, fresh.updatedMs) == (12_000, 1_002_000)


def test_state_from_json_clamps_index_and_repeat():
    s = PlayerState.from_json(
        {
            "playing": True,
            "queue": [{"title": "A"}],
            "index": 5,
            "repeat": "group",
            "volume": True,
        }
    )
    assert s.index == -1 and s.repeat == "none" and s.volume == 1.0
    assert PlayerState.from_json("nope") is None


def test_state_changed_ignores_position_explained_by_playback():
    prev = _state()
    assert not state_changed(prev, _state(positionMs=13_000, updatedMs=1_003_000))
    assert state_changed(prev, _state(positionMs=60_000, updatedMs=1_003_000))
    assert state_changed(prev, _state(playing=False))
    assert state_changed(prev, _state(volume=0.5))
    assert not state_changed(prev, _state(volume=0.999))
    assert state_changed(None, prev)


def test_queue_window_centres_the_current_entry():
    assert queue_window(50, 10) == (0, 50)
    assert queue_window(1000, 500) == (400, 600)
    assert queue_window(1000, 5) == (0, 200)
    assert queue_window(1000, 999) == (800, 1000)
    assert queue_window(1000, -1) == (0, 200)


def test_commands_are_validated_like_the_desktop_host():
    s = _state(index=1)
    assert validate_command("seek", {"positionMs": -5}) == ("seek", {"positionMs": 0})
    assert validate_command("volume", {"value": 3}) == ("volume", {"value": 1.0})
    assert validate_command("repeat", {"mode": "one"}, s)[1] == {"mode": "one"}
    assert validate_command("pause", {"continueOnPhone": True})[1] == {
        "continueOnPhone": True
    }
    assert validate_command("move", {"from": 0, "to": 2}, s)[1] == {"from": 0, "to": 2}
    for action, args in [
        ("dance", {}),
        ("seek", {}),
        ("volume", {"value": True}),
        ("shuffle", {"enabled": "yes"}),
        ("repeat", {"mode": "group"}),
        ("jump", {"index": 1.5}),
        ("jump", {"index": 3}),
        ("remove", {"index": 1}),  # the current song
        ("move", {"from": 0, "to": 9}),
    ]:
        with pytest.raises(CommandError) as err:
            validate_command(action, args, s)
        assert err.value.code == "invalid"


def test_handoff_keeps_unresolvable_entries_in_place():
    out = validate_handoff(
        {
            "queue": [{"title": "A"}, {"bogus": 1}],
            "index": 1,
            "positionMs": 5,
            "playing": False,
        }
    )
    assert out["queue"][1] is None and out["playing"] is False and out["index"] == 1
    assert validate_handoff({"queue": []})["playing"] is True
    with pytest.raises(CommandError):
        validate_handoff({"index": 0})


def test_handoff_args_continue_from_now():
    args = _state().handoff_args(at_ms=1_001_000)
    assert (
        args["positionMs"] == 11_000 and args["index"] == 0 and args["repeat"] == "none"
    )
    assert args["queue"][1] == {"title": "B", "artist": "Y"}
    assert _state().handoff_args(at_ms=1_001_000, playing=False)["playing"] is False
