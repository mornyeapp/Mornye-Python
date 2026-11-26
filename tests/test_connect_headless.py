"""The queue a Connect device with no page keeps (core/connect/headless.py),
driven through the hub with a fake engine."""

from __future__ import annotations

import random

import pytest

from mornye.core.connect.headless import HeadlessPlayer
from mornye.core.connect.hub import ConnectHub
from mornye.core.connect.model import CommandError, TrackRef


class FakeEngine:
    def __init__(self, emit) -> None:
        self.emit = emit
        self.calls: list[tuple] = []

    def load(self, path, next_path=None):
        self.calls.append(("load", path, next_path))

    def set_next(self, path):
        self.calls.append(("next", path))

    def command(self, name, value=None):
        self.calls.append((name, value))

    def close(self):
        self.calls.append(("close",))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


LIBRARY = {
    f"T{n}": {
        "path": f"/m/{n}.flac",
        "title": f"T{n}",
        "artist": "A",
        "duration_s": 100,
    }
    for n in range(1, 5)
}


def _resolve(refs):
    return [dict(LIBRARY[r.title]) if r and r.title in LIBRARY else None for r in refs]


def _refs(items):
    return [TrackRef(i["title"], i["artist"]) for i in items]


@pytest.fixture
def setup():
    clock = Clock()
    engines: list[FakeEngine] = []

    def factory(emit):
        engines.append(FakeEngine(emit))
        return engines[-1]

    player = HeadlessPlayer(
        "server-speaker",
        "NAS (server)",
        "server",
        factory,
        resolve=_resolve,
        refs_for=_refs,
        clock=clock,
        rng=random.Random(1),
    )
    hub = ConnectHub("admin", clock=clock)
    hub.set_broadcast(lambda *_: None)
    player.attach(hub)
    return player, hub, engines, clock


def _queue(*titles):
    return [{"title": t, "artist": "A"} for t in titles]


def _play(hub, titles, index=0, position=0):
    hub.transfer(
        "server-speaker",
        handoff={"queue": _queue(*titles), "index": index, "positionMs": position},
    )


def test_it_is_a_device_that_takes_a_handed_over_queue(setup):
    player, hub, engines, _ = setup
    assert hub.snapshot()["devices"][0]["kind"] == "server"
    _play(hub, ["T1", "Missing", "T3"], index=1, position=9000)
    engine = engines[0]
    # The missing song is skipped, and the position that went with it.
    assert engine.calls[0] == ("load", "/m/3.flac", None)
    assert ("seek", 9.0) not in engine.calls
    assert hub.active_id == "server-speaker"
    _, state = hub.active_state()
    assert [q.title for q in state.queue] == ["T1", "T3"] and state.index == 1


def test_transport_commands_reach_the_engine(setup):
    player, hub, engines, clock = setup
    _play(hub, ["T1", "T2", "T3"], position=5000)
    engine = engines[0]
    assert engine.calls[:3] == [
        ("load", "/m/1.flac", "/m/2.flac"),
        ("volume", 1.0),
        ("seek", 5.0),
    ]
    clock.t = 10
    assert player.position() == pytest.approx(15.0)
    hub.command("pause")
    assert engine.calls[-1] == ("pause", None) and player.playing is False
    hub.command("seek", {"positionMs": 42_000})
    assert engine.calls[-1] == ("seek", 42.0)
    hub.command("volume", {"value": 0.5})
    assert engine.calls[-1] == ("volume", 0.5)
    hub.command("next")
    assert ("load", "/m/2.flac", "/m/3.flac") in engine.calls
    hub.command("jump", {"index": 2})
    assert engine.calls[-2] == ("load", "/m/3.flac", None)


def test_songs_end_and_the_queue_moves_on_or_stops(setup):
    player, hub, engines, _ = setup
    _play(hub, ["T1", "T2"])
    engine = engines[0]
    engine.emit({"type": "advanced", "path": "/m/2.flac"})  # gapless change
    assert player.current["path"] == "/m/2.flac"
    engine.emit({"type": "ended"})
    assert player.playing is False
    hub.command("repeat", {"mode": "all"})
    engine.emit({"type": "ended"})
    assert player.current["path"] == "/m/1.flac" and player.playing is True


def test_shuffle_keeps_the_current_song_and_queue_edits_follow_the_window(setup):
    player, hub, engines, _ = setup
    _play(hub, ["T1", "T2", "T3", "T4"], index=1)
    hub.command("shuffle", {"enabled": True})
    assert player.current["path"] == "/m/2.flac" and player.pos == 0
    with pytest.raises(CommandError):
        hub.command("remove", {"index": 0})  # the current one
    hub.command("remove", {"index": 1})
    assert len(player.order) == 3
    hub.command("move", {"from": 2, "to": 1})
    assert player.current["path"] == "/m/2.flac"


def test_engine_state_events_update_what_the_hub_shows(setup):
    player, hub, engines, _ = setup
    _play(hub, ["T1"])
    engines[0].emit({"type": "pause", "time": 12.0})
    _, state = hub.active_state()
    assert state.playing is False and state.positionMs == 12_000
    engines[0].emit({"type": "loadedmetadata", "duration": 123.0})
    assert hub.active_state()[1].durationMs == 123_000


def test_close_leaves_the_hub_and_stops_the_engine(setup):
    player, hub, engines, _ = setup
    _play(hub, ["T1"])
    player.close()
    assert hub.snapshot()["devices"] == []
    assert engines[0].calls[-1] == ("close",)


def test_a_receivers_status_becomes_the_native_players_events(monkeypatch):
    from mornye.core.connect import speaker

    events: list[dict] = []
    engine = speaker.CastEngine("dev-1", events.append)
    engine._token = 7
    try:
        engine.connected = True
        engine.receive(
            {
                "connected": True,
                "token": 7,
                "state": 2,
                "position": 3000,
                "duration": 90000,
            }
        )
        assert [e["type"] for e in events] == ["loadedmetadata", "play"]
        engine.receive({"connected": True, "token": 6, "state": 3, "position": 0})
        assert len(events) == 2  # an older song's status is ignored
        engine.receive(
            {
                "connected": True,
                "token": 7,
                "state": 3,
                "position": 5000,
                "duration": 90000,
            }
        )
        assert events[-1] == {"type": "pause", "time": 5.0}
        engine.receive(
            {
                "connected": True,
                "token": 7,
                "state": 1,
                "finished": True,
                "duration": 90000,
            }
        )
        engine.receive(
            {
                "connected": True,
                "token": 7,
                "state": 1,
                "finished": True,
                "duration": 90000,
            }
        )
        assert [e["type"] for e in events].count("ended") == 1
        engine.receive({"connected": False})
        assert events[-1]["type"] == "error" and engine.connected is False
    finally:
        speaker._engines.pop("dev-1", None)


def test_the_server_casts_only_library_files_and_phone_uploads(monkeypatch, tmp_path):
    from mornye.core import subsonic
    from mornye.core.connect import resolver, speaker

    song = tmp_path / "lib" / "a.flac"
    song.parent.mkdir()
    song.write_bytes(b"x")
    received = tmp_path / "received"
    received.mkdir()
    (received / ("ab" * 16 + ".flac")).write_bytes(b"x")
    cat = resolver.Catalog(
        subsonic.build_index([(str(song), 1, {"title": "A"})]), "srv"
    )
    monkeypatch.setattr(resolver, "catalog", lambda server_id=None: cat)
    monkeypatch.setattr("mornye.core.link.link_dir", lambda: tmp_path)
    assert speaker._cast_allowed(song)
    assert speaker._cast_allowed(received / ("ab" * 16 + ".flac"))
    assert not speaker._cast_allowed(tmp_path / "secret.txt")


def test_the_speaker_switch_is_saved_and_the_env_forces_it(monkeypatch, tmp_path):
    from mornye.core.connect import speaker

    monkeypatch.setattr(speaker, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(speaker, "env_value", lambda name, default=None: "")
    assert speaker.speaker_enabled() is False
    speaker.set_speaker_enabled(True)
    assert speaker.speaker_enabled() is True
    speaker.set_speaker_enabled(False)
    assert speaker.speaker_enabled() is False
    monkeypatch.setattr(speaker, "env_value", lambda name, default=None: "1")
    assert speaker.speaker_enabled() is True and speaker.forced_by_env() is True
