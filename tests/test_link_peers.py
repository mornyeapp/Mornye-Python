"""Two computers running Mornye, paired over Link (core/connect/link_peers.py).

Both run here, in one process: computer B is a real LinkServer (its devices
in the Connect hub of account "b"), computer A a LinkPeers client (its
devices in the desktop hub). A pairs with B's PIN, then each can hand
playback to the other.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mornye.core import subsonic
from mornye.core.connect.hub import get_connect_hub, reset_connect_hubs
from mornye.core.connect.link_bridge import phone_device_id
from mornye.core.connect.link_peers import (
    PEERS_FILE,
    LinkPeers,
    Local,
    PeerError,
    peer_device_id,
)
from mornye.core.connect.model import now_ms
from mornye.core.connect.resolver import Catalog
from mornye.link_server import LinkServer

A_ID = "srv-a"


def _until(check, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def pair_of(tmp_path: Path):
    reset_connect_hubs()
    song = tmp_path / "music" / "alpha.flac"
    song.parent.mkdir()
    song.write_bytes(b"fLaC" + b"a" * 400)
    catalog_a = Catalog(
        subsonic.build_index(
            [(str(song), 1, {"title": "Alpha", "artist": "Ann", "duration_s": 200})]
        ),
        A_ID,
    )
    b = LinkServer(tmp_path / "b", bind="127.0.0.1", advertise=False, pair_interval_s=0)
    b.connect_owner = lambda: "b"
    empty = Catalog(subsonic.build_index([]), "srv-b")
    b.connect._catalog = lambda: empty
    assert b.start()["running"]
    hub_a, hub_b = get_connect_hub(None), get_connect_hub("b")
    events_a: list[tuple[str, dict]] = []
    events_b: list[tuple[str, dict]] = []
    hub_a.set_broadcast(lambda fn, p: events_a.append((fn, p)))
    hub_b.set_broadcast(lambda fn, p: events_b.append((fn, p)))
    hub_a.register("a-win", "Computer A", "window")
    hub_b.register("b-win", "Computer B", "window")
    peers = LinkPeers(
        tmp_path / "a",
        lambda: Local(
            device_id=A_ID,
            name="Computer A",
            platform="macos",
            app_version="test",
            hub=lambda: get_connect_hub(None),
            catalog=lambda: catalog_a,
        ),
    )
    yield {
        "a": peers,
        "b": b,
        "hub_a": hub_a,
        "hub_b": hub_b,
        "events_a": events_a,
        "events_b": events_b,
        "song": song,
    }
    peers.stop()
    b.stop()
    reset_connect_hubs()


def _pair(env) -> str:
    b = env["b"]
    pin = b.begin_pairing()["pairing"]["pin"]
    env["a"].pair("127.0.0.1", b.port, pin, b.fingerprint)
    server_id = b.store.server_id
    assert _until(lambda: env["hub_a"].device(peer_device_id(server_id)) is not None)
    assert _until(lambda: env["hub_b"].device(phone_device_id(A_ID)) is not None)
    return server_id


def _commands(events, target):
    return [
        (p["action"], p["args"])
        for fn, p in events
        if fn == "app_connect_command" and p["target"] == target
    ]


def test_pairing_lists_each_computer_on_the_other(pair_of):
    env = pair_of
    server_id = _pair(env)
    there = env["hub_a"].device(peer_device_id(server_id))
    assert there.kind == "mornye" and there.name == env["b"].name
    here = env["hub_b"].device(phone_device_id(A_ID))
    assert here.kind == "mornye" and here.name == "Computer A"
    (status,) = env["a"].status()
    assert status["state"] == "connected" and "apiKey" not in status
    saved = (env["a"].dir / PEERS_FILE).read_text()
    assert "BEGIN CERTIFICATE" in saved  # the pinned certificate
    # A short PIN, or no PIN shown there, is refused with a reason.
    with pytest.raises(PeerError, match="PIN"):
        env["a"].pair("127.0.0.1", env["b"].port, "12")
    with pytest.raises(PeerError, match="Show a PIN"):
        env["a"].pair("127.0.0.1", env["b"].port, "123456")


def test_play_on_the_other_computer_sends_the_song_it_lacks(pair_of):
    env = pair_of
    server_id = _pair(env)
    track = {
        "title": "Alpha",
        "artist": "Ann",
        "songId": subsonic._id("so-", str(env["song"])),
        "serverId": A_ID,
    }
    env["hub_a"].report_state(
        "a-win",
        {
            "track": track,
            "playing": True,
            "positionMs": 12_000,
            "durationMs": 200_000,
            "updatedMs": now_ms(),
            "queue": [track],
            "index": 0,
        },
    )
    env["hub_a"].transfer(peer_device_id(server_id))
    assert _until(lambda: _commands(env["events_b"], "b-win"))
    ((action, args),) = _commands(env["events_b"], "b-win")
    assert action == "play_queue" and args["positionMs"] >= 12_000
    file_id = args["queue"][0]["fileId"]
    received = env["b"].dir / "received" / f"{file_id}.flac"
    assert received.read_bytes() == env["song"].read_bytes()
    assert ("pause", {}) in _commands(env["events_a"], "a-win")
    # Commands follow it there.
    env["hub_b"].report_state(
        "b-win",
        {
            "track": track,
            "playing": True,
            "positionMs": 12_000,
            "queue": [track],
            "index": 0,
        },
    )
    env["hub_a"].command("pause", target=peer_device_id(server_id))
    assert _until(lambda: ("pause", {}) in _commands(env["events_b"], "b-win"))


def test_the_other_computer_can_hand_playback_back(pair_of):
    env = pair_of
    _pair(env)
    queue = [{"title": "Alpha", "artist": "Ann"}]
    env["hub_b"].report_state(
        "b-win",
        {
            "track": queue[0],
            "playing": True,
            "positionMs": 5_000,
            "queue": queue,
            "index": 0,
        },
    )
    env["hub_b"].transfer(phone_device_id(A_ID))
    assert _until(lambda: _commands(env["events_a"], "a-win"))
    ((action, args),) = _commands(env["events_a"], "a-win")
    assert action == "play_queue" and args["queue"][0]["title"] == "Alpha"
    assert env["hub_a"].active_id == "a-win"


def test_unpaired_there_stops_trying(pair_of):
    env = pair_of
    server_id = _pair(env)
    env["b"].revoke(A_ID)
    assert _until(lambda: env["a"].status()[0]["state"] == "unauthorized")
    assert env["hub_a"].device(peer_device_id(server_id)) is None
    assert env["a"].remove(server_id) is True
    assert env["a"].status() == []


def test_connecting_without_a_pin_waits_for_a_click_there(pair_of):
    env = pair_of
    b = env["b"]
    env["a"].ask("127.0.0.1", b.port, b.fingerprint, "Computer B")
    assert _until(lambda: b.status()["requests"])
    ((waiting,),) = [env["a"].status()]
    assert waiting["state"] == "waiting"
    b.answer_request(b.status()["requests"][0]["id"], True)
    server_id = b.store.server_id
    assert _until(lambda: env["hub_a"].device(peer_device_id(server_id)) is not None)
    (paired,) = env["a"].status()
    assert paired["serverId"] == server_id and paired["state"] == "connected"


def test_a_refusal_there_is_shown_and_can_be_dismissed(pair_of):
    env = pair_of
    b = env["b"]
    env["a"].ask("127.0.0.1", b.port, b.fingerprint, "Computer B")
    assert _until(lambda: b.status()["requests"])
    b.answer_request(b.status()["requests"][0]["id"], False)
    assert _until(lambda: env["a"].status()[0]["state"] == "refused")
    key = env["a"].status()[0]["serverId"]
    assert env["a"].remove(key) and env["a"].status() == []


def test_a_trusting_computer_lets_it_in_at_once(pair_of):
    env = pair_of
    b = env["b"]
    b.set_trust_network(True)
    env["a"].ask("127.0.0.1", b.port, b.fingerprint)
    assert _until(
        lambda: env["a"].status() and env["a"].status()[0]["state"] == "connected"
    )


def test_what_plays_here_shows_on_the_other_computer(pair_of):
    env = pair_of
    _pair(env)
    track = {"title": "Alpha", "artist": "Ann"}
    env["hub_a"].report_state(
        "a-win",
        {
            "track": track,
            "playing": True,
            "positionMs": 3_000,
            "queue": [track],
            "index": 0,
        },
    )
    here_there = phone_device_id(A_ID)

    def shown() -> bool:
        device = env["hub_b"].device(here_there)
        return device is not None and device.state is not None and device.state.playing

    assert _until(shown)
    # Nothing played there: a page opened there now follows this computer.
    assert env["hub_b"].active_id == here_there

    def state_reported() -> bool:
        states = [p for fn, p in env["events_b"] if fn == "app_connect_state"]
        return bool(states and states[-1]["deviceId"] == here_there)

    assert _until(state_reported)
    states = [p for fn, p in env["events_b"] if fn == "app_connect_state"]
    assert states and states[-1]["deviceId"] == here_there
    # And it can be paused from there.
    env["hub_b"].command("pause", target=here_there)
    assert _until(lambda: ("pause", {}) in _commands(env["events_a"], "a-win"))


def test_the_computer_it_moved_to_keeps_its_own_controls(pair_of):
    """A hands playback to B; B pauses, skips, turns it down on its own, and
    stays the one that plays (A shows each change), as in Spotify Connect —
    even though A's window last said it was playing."""
    env = pair_of
    server_id = _pair(env)
    track = {"title": "Alpha", "artist": "Ann"}
    playing = {
        "track": track,
        "playing": True,
        "positionMs": 3_000,
        "queue": [track],
        "index": 0,
    }
    env["hub_a"].report_state("a-win", playing)
    env["hub_a"].transfer(peer_device_id(server_id))
    assert _until(lambda: _commands(env["events_b"], "b-win"))
    env["hub_b"].report_state("b-win", playing)
    there = peer_device_id(server_id)

    def a_sees(**want) -> bool:
        device = env["hub_a"].device(there)
        state = device.state if device is not None else None
        return state is not None and all(
            getattr(state, k) == v for k, v in want.items()
        )

    assert _until(lambda: a_sees(playing=True))
    # Paused on B itself: A never sends "playing" back over it.
    env["hub_b"].report_state("b-win", {**playing, "playing": False})
    assert _until(lambda: a_sees(playing=False))
    env["a"].connection(server_id)._report(force=True)  # A's heartbeat
    time.sleep(0.5)
    assert env["hub_b"].active_id == "b-win"
    here_there = env["hub_b"].device(phone_device_id(A_ID))
    assert here_there.state is None or not here_there.state.playing
    # Turned down on B: A shows it, and A still controls B.
    env["hub_b"].report_state("b-win", {**playing, "volume": 0.3})
    assert _until(lambda: a_sees(playing=True, volume=0.3))
    assert env["hub_a"].active_id == there
