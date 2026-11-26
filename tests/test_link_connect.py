"""Mornye Link's player ops answered by Connect (core/connect/link_bridge.py),
checked against the Mornye app's desktop host (link_desktop_ops.dart,
link_desktop_host.dart): the phone controls what plays, hands its queue
over, and is a device the computer can hand back to."""

from __future__ import annotations

import pytest

from mornye.core import subsonic
from mornye.core.connect.hub import get_connect_hub, reset_connect_hubs
from mornye.core.connect.link_bridge import LinkConnect, phone_device_id
from mornye.core.connect.model import CommandError, now_ms
from mornye.core.connect.resolver import Catalog

SERVER = "srv-1"
FILE_ID = "ab" * 16


@pytest.fixture
def bridge(tmp_path):
    reset_connect_hubs()
    catalog = Catalog(
        subsonic.build_index(
            [
                (
                    "/m/a.flac",
                    1,
                    {"title": "Alpha", "artist": "Ann", "duration_s": 200},
                ),
                ("/m/b.flac", 1, {"title": "Beta", "artist": "Bob", "duration_s": 100}),
            ]
        ),
        SERVER,
    )
    frames: list[tuple[str, dict]] = []
    phones: list[str] = []
    b = LinkConnect(
        lambda device, message: frames.append((device, message)),
        lambda: list(phones),
        received_dir=lambda: tmp_path / "received",
        catalog=lambda: catalog,
    )
    hub = get_connect_hub(None)
    page_events: list[tuple[str, dict]] = []
    hub.set_broadcast(lambda fn, payload: page_events.append((fn, payload)))
    hub.register("mac", "MacBook", "window")
    b.frames, b.phones, b.page_events = frames, phones, page_events
    yield b
    b.close()
    reset_connect_hubs()


def _ref(title, artist, path):
    return {
        "title": title,
        "artist": artist,
        "songId": subsonic._id("so-", path),
        "serverId": SERVER,
    }


def _page_plays(index=0, position=10_000, playing=True):
    queue = [_ref("Alpha", "Ann", "/m/a.flac"), _ref("Beta", "Bob", "/m/b.flac")]
    get_connect_hub(None).report_state(
        "mac",
        {
            "track": queue[index],
            "playing": playing,
            "positionMs": position,
            "durationMs": 200_000,
            "updatedMs": now_ms(),
            "queue": queue,
            "index": index,
        },
    )


def _page_commands(bridge):
    return [
        (p["action"], p["args"])
        for fn, p in bridge.page_events
        if fn == "app_connect_command" and p["target"] == "mac"
    ]


def _op(bridge, op, method="POST", args=None):
    return bridge.op(op, method, args or {}, "p1")


def test_state_is_the_desktop_players_read_now(bridge):
    idle = _op(bridge, "state", "GET")
    assert idle["playing"] is False and idle["queue"] == []
    _page_plays(index=1)
    state = _op(bridge, "state", "GET")
    assert state["index"] == 1 and state["track"]["title"] == "Beta"
    assert state["queue"][0]["serverId"] == SERVER
    assert abs(state["updatedMs"] - now_ms()) < 1000


def test_commands_reach_the_page_that_plays(bridge):
    _page_plays()
    assert _op(
        bridge, "command", args={"action": "seek", "args": {"positionMs": 5}}
    ) == {"ok": True}
    _op(bridge, "command", args={"action": "pause", "args": {"continueOnPhone": True}})
    assert _page_commands(bridge) == [("seek", {"positionMs": 5}), ("pause", {})]
    for args in (
        {"action": ""},
        {"action": "dance"},
        {"action": "jump", "args": {"index": 9}},
    ):
        with pytest.raises(CommandError) as err:
            _op(bridge, "command", args=args)
        assert err.value.code == "invalid"
    with pytest.raises(CommandError) as err:
        _op(bridge, "downloads", "GET")
    assert err.value.code == "not_found"


def test_handoff_check_then_play_then_append(bridge, tmp_path):
    queue = [
        {"title": "Alpha", "artist": "Ann"},
        {"title": "Phone only", "fileId": FILE_ID},
        {"title": "Beta", "artist": "Bob"},
    ]
    check = _op(
        bridge, "handoff/play", args={"queue": queue, "index": 1, "check": True}
    )
    assert check == {"resolved": 2, "missing": [1]}
    assert _page_commands(bridge) == []  # a check plays nothing
    # The phone sends the song this computer lacks, then hands over.
    received = tmp_path / "received"
    received.mkdir()
    (received / f"{FILE_ID}.flac").write_bytes(b"x")
    out = _op(
        bridge,
        "handoff/play",
        args={"queue": queue, "index": 1, "positionMs": 4000, "playing": True},
    )
    assert out["resolved"] == 3 and out["missing"] == [] and out["handoffId"]
    ((action, args),) = _page_commands(bridge)
    assert action == "play_queue" and args["index"] == 1 and args["positionMs"] == 4000
    assert get_connect_hub(None).active_id == "mac"
    more = _op(
        bridge,
        "handoff/append",
        args={
            "handoffId": out["handoffId"],
            "queue": [{"title": "Beta", "artist": "Bob"}],
        },
    )
    assert more == {"resolved": 1}
    assert _page_commands(bridge)[-1][0] == "queue_add"
    with pytest.raises(CommandError):
        _op(bridge, "handoff/append", args={"handoffId": "old", "queue": []})


def test_nothing_resolved_hands_nothing_over(bridge):
    out = _op(bridge, "handoff/play", args={"queue": [{"title": "Nope"}], "index": 0})
    assert out == {"resolved": 0, "missing": [0]}
    assert _page_commands(bridge) == []


def test_current_resolve_and_details(bridge):
    _page_plays(position=30_000, playing=False)
    current = _op(bridge, "handoff/current", "GET")
    assert current["positionMs"] == 30_000 and current["playing"] is False
    ids = _op(
        bridge,
        "resolve",
        args={"tracks": [{"title": "Beta", "artist": "Bob"}, {"title": "x"}]},
    )
    assert ids == {"songIds": [subsonic._id("so-", "/m/b.flac"), None]}
    details = _op(
        bridge, "player/details", args={"track": {"title": "Alpha", "artist": "Ann"}}
    )
    assert details["format"] == "FLAC"
    with pytest.raises(CommandError):
        _op(bridge, "player/details")


def test_a_phone_is_a_device_that_never_takes_over_by_itself(bridge):
    bridge.phones.append("p1")
    bridge.phone_connected("p1", "Pixel")
    assert bridge.frames[-1][1]["type"] == "player.state"
    hub = get_connect_hub(None)
    assert hub.device(phone_device_id("p1")).kind == "phone"
    _page_plays(playing=False)
    phone_state = {
        "track": {"title": "X"},
        "playing": True,
        "positionMs": 0,
        "queue": [{"title": "X"}],
        "index": 0,
    }
    bridge.phone_message("p1", {"type": "player.state", "state": phone_state})
    assert hub.active_id == "mac"  # the phone playing on its own changes nothing
    # The computer starting to play pauses the playing phone.
    _page_plays(playing=True, index=1)
    assert ("p1", {"type": "command", "action": "pause", "args": {}}) in bridge.frames
    # "Play on" → the phone: Continue on phone.
    hub.transfer(phone_device_id("p1"))
    play = [m for _, m in bridge.frames if m.get("action") == "play_queue"][-1]
    assert play["args"]["index"] == 1 and play["args"]["queue"][1]["title"] == "Beta"
    bridge.phone_disconnected("p1")
    assert hub.device(phone_device_id("p1")) is None


def test_state_pushes_skip_what_playback_explains(bridge):
    bridge.phones.append("p1")
    _page_plays()

    def pushed():
        return len([m for _, m in bridge.frames if m["type"] == "player.state"])

    bridge.push()
    first = pushed()
    bridge.push()
    assert pushed() == first
    bridge.push(force=True)
    assert pushed() == first + 1


def test_uploads_must_be_named_by_file_id(bridge):
    assert bridge.upload_path(f"{FILE_ID}.FLAC").name == f"{FILE_ID}.flac"
    for bad in ("../x.flac", f"{FILE_ID}.exe", "short.flac", FILE_ID):
        with pytest.raises(CommandError):
            bridge.upload_path(bad)
