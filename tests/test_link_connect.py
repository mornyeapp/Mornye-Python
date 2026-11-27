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


def test_a_phone_playing_on_its_own_pauses_nothing(bridge):
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
    # Nothing else plays: the phone is what the pages show now (a page
    # opened now follows it), and nothing was paused for it.
    assert hub.active_id == phone_device_id("p1")
    assert _page_commands(bridge) == []
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


# ── "Play on" from the phone: devices, transfer, target ───────────────────


def _connect_phone(bridge, device_id="p1", name="Pixel"):
    bridge.phones.append(device_id)
    bridge.phone_connected(device_id, name)


def test_devices_lists_the_account_and_marks_the_phone_asking(bridge):
    _connect_phone(bridge)
    _page_plays()
    out = _op(bridge, "devices", "GET")
    assert out["activeId"] == "mac" and out["targetId"] == "mac"
    by_id = {d["id"]: d for d in out["devices"]}
    assert by_id["mac"]["active"] and not by_id["mac"]["self"]
    assert by_id["mac"]["state"]["track"]["title"] == "Alpha"
    assert by_id[phone_device_id("p1")]["self"] is True
    assert all("extra" not in d and "meta" not in d for d in out["devices"])


def test_a_new_device_is_pushed_to_the_phones(bridge):
    _connect_phone(bridge)
    first = [m for _, m in bridge.frames if m["type"] in ("player.state", "devices")]
    assert [m["type"] for m in first[:2]] == ["player.state", "devices"]
    get_connect_hub(None).register("tab", "Firefox", "browser")
    frame = [m for d, m in bridge.frames if d == "p1" and m["type"] == "devices"][-1]
    assert "tab" in {d["id"] for d in frame["devices"]}


def test_transfer_moves_playback_to_the_device_picked(bridge):
    hub = get_connect_hub(None)
    hub.register("tab", "Firefox", "browser")
    _page_plays(index=1, position=42_000)
    out = _op(bridge, "transfer", args={"target": "tab"})
    assert out["activeId"] == "tab"
    to_tab = [
        p
        for fn, p in bridge.page_events
        if fn == "app_connect_command" and p["target"] == "tab"
    ]
    assert to_tab[-1]["action"] == "play_queue" and to_tab[-1]["args"]["index"] == 1
    assert ("pause", {}) in _page_commands(bridge)
    with pytest.raises(CommandError) as err:
        _op(bridge, "transfer", args={"target": "nowhere"})
    assert err.value.code == "not_found"
    with pytest.raises(CommandError):
        _op(bridge, "transfer", args={})


def test_ops_reach_the_device_named_as_target(bridge):
    hub = get_connect_hub(None)
    hub.register("tab", "Firefox", "browser")
    _page_plays()
    hub.report_state(
        "tab",
        {
            "track": {"title": "Beta"},
            "playing": False,
            "positionMs": 0,
            "queue": [{"title": "Beta"}],
            "index": 0,
        },
        claim=False,
    )
    _op(bridge, "command", args={"action": "play", "target": "tab"})
    tab = [
        (p["action"])
        for fn, p in bridge.page_events
        if fn == "app_connect_command" and p["target"] == "tab"
    ]
    assert tab == ["play"] and _page_commands(bridge) == []
    assert _op(bridge, "state", "GET", {"target": "tab"})["track"]["title"] == "Beta"
    assert (
        _op(bridge, "handoff/current", "GET", {"target": "tab"})["queue"][0]["title"]
        == "Beta"
    )
    out = _op(
        bridge,
        "handoff/play",
        args={
            "queue": [{"title": "Alpha", "artist": "Ann"}],
            "index": 0,
            "target": "tab",
        },
    )
    assert out["resolved"] == 1 and hub.active_id == "tab"
    _op(
        bridge,
        "handoff/append",
        args={
            "handoffId": out["handoffId"],
            "queue": [{"title": "Beta", "artist": "Bob"}],
        },
    )
    tab = [
        p["action"]
        for fn, p in bridge.page_events
        if fn == "app_connect_command" and p["target"] == "tab"
    ]
    assert tab[-2:] == ["play_queue", "queue_add"]


def test_a_phone_handed_playback_is_what_the_other_phones_control(bridge):
    _connect_phone(bridge, "p1", "Pixel")
    _connect_phone(bridge, "p2", "iPhone")
    _page_plays()
    _op(bridge, "transfer", args={"target": phone_device_id("p2")})
    hub = get_connect_hub(None)
    assert hub.active_id == phone_device_id("p2")
    bridge.phone_message(
        "p2",
        {
            "type": "player.state",
            "state": {
                "track": {"title": "Alpha"},
                "playing": True,
                "positionMs": 1000,
                "queue": [{"title": "Alpha"}],
                "index": 0,
            },
        },
    )
    # p1 sees and controls p2; p2 still sees the computer.
    assert bridge.target("p1")[0] == phone_device_id("p2")
    assert bridge.target("p2")[0] == "mac"
    _op(bridge, "command", args={"action": "pause"})
    assert ("p2", {"type": "command", "action": "pause", "args": {}}) in bridge.frames
    bridge.push(force=True)
    last = {d: m for d, m in bridge.frames if m["type"] == "player.state"}
    assert last["p1"]["state"]["playing"] is True
    assert last["p2"]["state"]["track"]["title"] == "Alpha"


def test_a_phone_does_not_take_over_a_computer_that_plays(bridge):
    bridge.phones.append("p1")
    bridge.phone_connected("p1", "Pixel")
    _page_plays(playing=True)
    bridge.phone_message(
        "p1",
        {
            "type": "player.state",
            "state": {
                "track": {"title": "X"},
                "playing": True,
                "positionMs": 0,
                "queue": [{"title": "X"}],
                "index": 0,
            },
        },
    )
    assert get_connect_hub(None).active_id == "mac"
    assert _page_commands(bridge) == []
