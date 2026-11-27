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
