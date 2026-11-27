"""core/discord_presence.py — Discord Rich Presence over the local IPC socket.

A small stand-in for the Discord client listens on a Unix socket, so the
handshake, the activity sent and Discord's replies are the real wire format.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

import pytest

from mornye.core import discord_presence as dp

unix_only = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "AF_UNIX"),
    reason="the stand-in Discord listens on a Unix socket",
)


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_config_file", lambda: tmp_path / "discord-presence.json")
    monkeypatch.setattr(dp, "COALESCE_SECONDS", 0.01)


def _read_frame(conn: socket.socket) -> tuple[int, dict]:
    header = b""
    while len(header) < 8:
        part = conn.recv(8 - len(header))
        if not part:
            raise ConnectionError
        header += part
    op, length = struct.unpack("<II", header)
    body = b""
    while len(body) < length:
        body += conn.recv(length - len(body))
    return op, json.loads(body or b"{}")


class FakeDiscord:
    """Answers the handshake with READY (or a close, when told to refuse)
    and every SET_ACTIVITY with its nonce, keeping what was set."""

    def __init__(self, path: str, refuse: str | None = None):
        self.path = path
        self.refuse = refuse
        self.handshakes: list[dict] = []
        self.activities: list[dict | None] = []
        self.changed = threading.Condition()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(4)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        try:
            op, body = _read_frame(conn)
            assert op == dp.OP_HANDSHAKE
            self.handshakes.append(body)
            if self.refuse:
                conn.sendall(
                    dp.encode_frame(dp.OP_CLOSE, {"code": 4000, "message": self.refuse})
                )
                return
            conn.sendall(
                dp.encode_frame(
                    dp.OP_FRAME,
                    {
                        "cmd": "DISPATCH",
                        "evt": "READY",
                        "data": {"user": {"username": "ada", "global_name": "Ada"}},
                    },
                )
            )
            while True:
                op, body = _read_frame(conn)
                with self.changed:
                    self.activities.append(body["args"]["activity"])
                    self.changed.notify_all()
                conn.sendall(
                    dp.encode_frame(
                        dp.OP_FRAME,
                        {"cmd": "SET_ACTIVITY", "nonce": body["nonce"], "data": {}},
                    )
                )
        except (ConnectionError, OSError, AssertionError):
            pass
        finally:
            conn.close()

    def wait_for(self, count: int, timeout: float = 5.0) -> list:
        with self.changed:
            self.changed.wait_for(lambda: len(self.activities) >= count, timeout)
            return list(self.activities)

    def close(self) -> None:
        self.server.close()


@pytest.fixture
def socket_dir(monkeypatch):
    # Short: a Unix socket path is limited to about a hundred characters.
    # Only this path is tried, so a real Discord on the machine is left alone.
    folder = tempfile.mkdtemp(prefix="mdp", dir="/tmp")
    path = os.path.join(folder, "discord-ipc-0")
    monkeypatch.setattr(dp, "candidate_paths", lambda: [path])
    yield path
    shutil.rmtree(folder, ignore_errors=True)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


SONG = {"title": "Song", "artist": "Artist", "album": "Record", "duration": 180}


# ── Pure parts ──────────────────────────────────────────────────────────────


def test_frame_is_little_endian_opcode_and_length_then_json() -> None:
    frame = dp.encode_frame(1, {"a": 1})
    assert frame[:8] == struct.pack("<II", 1, len(b'{"a":1}'))
    assert frame[8:] == b'{"a":1}'


def test_activity_is_listening_with_timestamps_and_album() -> None:
    activity = dp.build_activity(
        {**SONG, "duration": 200}, start=1000.0, artwork="https://example.test/c.jpg"
    )
    assert activity["type"] == 2
    assert activity["details"] == "Song"
    assert activity["state"] == "Artist"
    assert activity["timestamps"] == {"start": 1_000_000, "end": 1_200_000}
    assert activity["assets"] == {
        "large_image": "https://example.test/c.jpg",
        "large_text": "Record",
    }


def test_activity_fields_fit_discords_limits() -> None:
    activity = dp.build_activity(
        {"title": "X", "artist": "", "album": "", "duration": 0}, start=0
    )
    assert len(activity["details"]) >= 2
    assert "state" not in activity and "assets" not in activity
    assert "end" not in activity["timestamps"]
    assert len(dp.field("y" * 300)) == 128


def test_artwork_only_from_the_same_artist() -> None:
    class Response:
        def __init__(self, results):
            self._results = results

        def json(self):
            return {"results": self._results}

    class Client:
        def __init__(self):
            self.entities = []

        def get(self, url, params):
            self.entities.append(params["entity"])
            if params["entity"] == "album":
                return Response(
                    [
                        {
                            "artistName": "Someone Else",
                            "collectionName": "Record",
                            "artworkUrl100": "x/100x100bb.jpg",
                        }
                    ]
                )
            return Response(
                [
                    {
                        "artistName": "The Artist",
                        "trackName": "Song (Remastered)",
                        "artworkUrl100": "y/100x100bb.jpg",
                    }
                ]
            )

    client = Client()
    assert (
        dp.lookup_artwork("The Artist", "Record", "Song", client=client)
        == "y/512x512bb.jpg"
    )
    assert client.entities == ["album", "song"]


def test_a_multi_artist_tag_is_shown_with_commas_and_searched_by_its_main_artist() -> (
    None
):
    activity = dp.build_activity({**SONG, "artist": "Drake;Lil Durk"}, start=0)
    assert activity["state"] == "Drake, Lil Durk"

    class Client:
        terms: list[str] = []

        def get(self, url, params):
            self.terms.append(params.get("term") or params.get("q"))

            class Response:
                def json(self):
                    return {"results": []}

            return Response()

    client = Client()
    dp.lookup_artwork("Drake;Lil Durk", "Record", "Song", client=client)
    assert client.terms == ["Drake Record", "Drake Song"] * 2


def test_artwork_falls_back_to_deezer_when_apple_has_not_the_record() -> None:
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def get(self, url, params):
            if "itunes" in url:
                return Response({"results": []})
            if url.endswith("search/album"):
                return Response(
                    {
                        "data": [
                            {
                                "title": "MI Fist (2004 Remaster)",
                                "artist": {"name": "Club Dogo"},
                                "cover_xl": "https://deezer.test/cover.jpg",
                            }
                        ]
                    }
                )
            return Response({"data": []})

    assert (
        dp.lookup_artwork(
            "Club Dogo",
            "MI Fist (2004 Remaster)",
            "LLCD (Ladies Love Club Dogo) - 2004 Remaster",
            client=Client(),
        )
        == "https://deezer.test/cover.jpg"
    )


def test_settings_survive_a_restart() -> None:
    presence = dp.DiscordPresence()
    presence.configure(enabled=True, application_id=" 123 456 ", show_artwork=False)
    presence.shutdown()
    again = dp.load_config()
    assert again.enabled and again.application_id == "123456" and not again.show_artwork


def test_a_non_numeric_id_is_asked_for_again() -> None:
    presence = dp.DiscordPresence()
    presence.configure(enabled=True, application_id="not-an-id")
    assert presence.status()["status"] == "missing_id"
    presence.shutdown()


# ── Over the socket ─────────────────────────────────────────────────────────


@unix_only
def test_connects_shows_the_song_and_clears_it_on_pause(socket_dir) -> None:
    discord = FakeDiscord(socket_dir)
    presence = dp.DiscordPresence(artwork=lambda *a: None)
    try:
        presence.configure(enabled=True, application_id="1234567890")
        assert _wait(lambda: presence.status()["connected"])
        assert presence.status()["status_text"] == "Connected as Ada"
        assert discord.handshakes == [{"v": 1, "client_id": "1234567890"}]

        # Nothing plays yet: whatever an earlier run left is cleared.
        assert discord.wait_for(1) == [None]

        presence.now_playing({**SONG, "position": 10, "playing": True})
        sent = discord.wait_for(2)
        assert sent[-1]["details"] == "Song"
        start = sent[-1]["timestamps"]["start"] / 1000
        assert abs(start - (time.time() - 10)) < 3

        # Told the same thing again: nothing new reaches Discord.
        presence.now_playing({**SONG, "position": 10.5, "playing": True})
        time.sleep(0.3)
        assert len(discord.activities) == 2

        presence.now_playing({**SONG, "position": 11, "playing": False})
        assert discord.wait_for(3)[-1] is None
    finally:
        presence.shutdown()
        discord.close()


@unix_only
def test_waits_while_discord_is_closed(socket_dir) -> None:
    presence = dp.DiscordPresence(artwork=lambda *a: None)
    try:
        presence.configure(enabled=True, application_id="1234567890")
        assert _wait(lambda: presence.status()["status"] == "waiting")
    finally:
        presence.shutdown()


@unix_only
def test_a_refused_client_id_is_reported_and_not_retried(
    socket_dir, monkeypatch
) -> None:
    monkeypatch.setattr(dp, "RETRY_SECONDS", 0.05)
    discord = FakeDiscord(socket_dir, refuse="Invalid Client ID")
    presence = dp.DiscordPresence(artwork=lambda *a: None)
    try:
        presence.configure(enabled=True, application_id="1")
        assert _wait(lambda: presence.status()["status"] == "failed")
        assert presence.status()["status_text"] == "Invalid Client ID"
        time.sleep(0.3)
        assert len(discord.handshakes) == 1
    finally:
        presence.shutdown()
        discord.close()
