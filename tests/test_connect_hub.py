"""The Connect hub (core/connect/hub.py): devices, the active one, routing
commands and moving playback."""

from __future__ import annotations

import pytest

from mornye.core.connect.hub import ConnectHub, get_connect_hub, reset_connect_hubs
from mornye.core.connect.model import CommandError, now_ms


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _state(playing=True, index=0, position=0, titles=("A", "B", "C")):
    return {
        "track": {"title": titles[index], "artist": "X"},
        "playing": playing,
        "positionMs": position,
        "durationMs": 300_000,
        "updatedMs": now_ms(),
        "queue": [{"title": t, "artist": "X"} for t in titles],
        "index": index,
    }


@pytest.fixture
def hub():
    events: list[tuple[str, dict]] = []
    h = ConnectHub("ann", clock=Clock())
    h.set_broadcast(lambda fn, payload: events.append((fn, payload)))
    h.events = events
    return h


def _commands(hub, target=None):
    return [
        (p["action"], p["args"])
        for fn, p in hub.events
        if fn == "app_connect_command" and (target is None or p["target"] == target)
    ]


def test_register_and_snapshot(hub):
    hub.register("w", "Mac", "window")
    snap = hub.register("b", "  ", "browser")
    assert [d["name"] for d in snap["devices"]] == ["Mac", "Browser"]
    assert snap["activeId"] is None
    assert hub.events[-1][0] == "app_connect_devices"
    with pytest.raises(CommandError):
        hub.register("", "x", "window")
    with pytest.raises(CommandError):
        hub.register("x", "x", "toaster")


def test_starting_to_play_makes_a_device_active_and_pauses_the_others(hub):
    hub.register("w", "Mac", "window")
    hub.register("b", "Tab", "browser")
    hub.report_state("w", _state())
    assert hub.active_id == "w"
    hub.events.clear()
    hub.report_state("b", _state())
    assert hub.active_id == "b"
    assert _commands(hub, "w") == [("pause", {})]
    assert any(
        fn == "app_connect_state" and p["deviceId"] == "b" for fn, p in hub.events
    )


def test_only_the_active_devices_changes_are_broadcast(hub):
    hub.register("w", "Mac", "window")
    hub.register("b", "Tab", "browser")
    hub.report_state("w", _state())
    hub.events.clear()
    hub.report_state("b", _state(playing=False))
    assert not [e for e in hub.events if e[0] == "app_connect_state"]
    assert hub.report_state("w", _state()) is False  # nothing new


def test_commands_go_to_the_active_device_checked_against_its_window(hub):
    hub.register("w", "Mac", "window")
    with pytest.raises(CommandError) as err:
        hub.command("play")
    assert err.value.code == "unavailable"
    hub.report_state("w", _state(index=1))
    hub.events.clear()
    assert hub.command("seek", {"positionMs": 5000}) == {"ok": True}
    assert _commands(hub) == [("seek", {"positionMs": 5000})]
    with pytest.raises(CommandError):
        hub.command("remove", {"index": 1})
    with pytest.raises(CommandError) as err:
        hub.command("play", target="nobody")
    assert err.value.code == "not_found"


def test_a_device_with_its_own_sender_gets_its_commands_directly(hub):
    got = []
    hub.register("p", "Phone", "phone", sender=lambda a, args: got.append((a, args)))
    hub.report_state("p", _state())
    hub.command("next")
    assert got == [("next", {})]
    assert _commands(hub) == []


def test_transfer_moves_queue_and_position_and_pauses_the_source(hub):
    hub.register("w", "Mac", "window")
    hub.register("b", "Tab", "browser")
    hub.report_state("w", _state(index=1, position=42_000))
    hub.events.clear()
    snap = hub.transfer("b")
    assert snap["activeId"] == "b"
    assert _commands(hub, "w") == [("pause", {})]
    ((action, args),) = _commands(hub, "b")
    assert action == "play_queue" and args["index"] == 1
    assert 42_000 <= args["positionMs"] < 43_000 and args["playing"] is True
    assert [q["title"] for q in args["queue"]] == ["A", "B", "C"]


def test_transfer_with_the_pages_own_queue_and_refusals(hub):
    hub.register("w", "Mac", "window")
    hub.register("r", "Remote only", "browser", can_play=False)
    with pytest.raises(CommandError):
        hub.transfer("w")  # nothing playing, no handoff
    hub.transfer(
        "w",
        handoff={"queue": [{"title": "Z"}], "index": 0, "positionMs": 0},
        playing=False,
    )
    ((action, args),) = _commands(hub, "w")
    assert args["playing"] is False and args["queue"] == [{"title": "Z"}]
    with pytest.raises(CommandError):
        hub.transfer("r")


def test_a_new_queue_for_the_device_already_playing_is_played(hub):
    # A song or album picked on the other computer arrives as a transfer to
    # the device that plays already: it must replace the queue, not be
    # dropped as a transfer to itself.
    hub.register("w", "Mac", "window")
    hub.report_state("w", _state())
    hub.events.clear()
    snap = hub.transfer("w")
    assert snap["activeId"] == "w" and _commands(hub) == []
    hub.transfer("w", handoff={"queue": [{"title": "Z"}], "index": 0, "positionMs": 0})
    assert _commands(hub) == [
        ("play_queue", {"queue": [{"title": "Z"}], "index": 0, "positionMs": 0})
    ]


def test_stale_pages_expire_but_devices_with_a_transport_stay():
    clock = Clock()
    hub = ConnectHub(None, clock=clock)
    hub.register("b", "Tab", "browser")
    hub.register("p", "Phone", "phone", sender=lambda *_: None)
    hub.report_state("b", _state())
    clock.t = 100
    assert hub.expire(45) == ["b"]
    assert hub.active_id is None
    assert [d["id"] for d in hub.snapshot()["devices"]] == ["p"]


def test_listeners_hear_state_and_device_changes(hub):
    heard = []
    remove = hub.add_listener(lambda event, payload: heard.append(event))
    hub.register("w", "Mac", "window")
    hub.report_state("w", _state())
    assert heard == ["devices", "devices", "state"]
    remove()
    hub.unregister("w")
    assert heard[-1] == "state" and hub.active_id is None


def test_hubs_are_per_owner():
    reset_connect_hubs()
    assert get_connect_hub("a") is get_connect_hub("a")
    assert get_connect_hub("a") is not get_connect_hub("b")
    assert get_connect_hub() is get_connect_hub(None)
    reset_connect_hubs()
