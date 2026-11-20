"""The native audio engine (core/native_player.py, api_mixins/native_player.py).

Most tests drive a fake mpv, so they run everywhere; the last group plays
real files through libmpv with ``ao=null`` when python-mpv and libmpv are
present (docs/native-player-plan.md, phase 1).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mornye.core import native_player as np_mod
from mornye.core.native_player import (
    EQ_PRESETS,
    NativePlayer,
    NativePlayerUnavailable,
    PlayerOptions,
    filter_chain,
    is_bitstreamed,
    mpv_properties,
    output_label,
)


@pytest.fixture(autouse=True)
def _options_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(np_mod, "_options_file", lambda: tmp_path / "player-audio.json")


class FakeMPV:
    """What NativePlayer asks of python-mpv's MPV, recorded."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.props: dict = {
            "audio-device-list": [{"name": "auto", "description": "Auto"}]
        }
        self.commands: list[tuple] = []
        self.observers: dict = {}
        self.event_cb = None
        self.terminated = False
        self.pause = False
        self.volume = 100.0
        self.mute = False
        self.speed = 1.0

    def __setitem__(self, key, value):
        self.props[key] = value

    def __getitem__(self, key):
        return self.props[key]

    def __getattr__(self, name):
        # Live properties, read as attributes like python-mpv's.
        props = self.__dict__.get("props", {})
        key = name.replace("_", "-")
        if key in props:
            return props[key]
        raise AttributeError(name)

    def command(self, *args):
        self.commands.append(args)

    def observe_property(self, name, handler):
        self.observers[name] = handler

    def register_event_callback(self, cb):
        self.event_cb = cb

    def terminate(self):
        self.terminated = True

    # test helpers
    def change(self, name, value):
        self.observers[name](name, value)

    def event(self, **data):
        self.event_cb(data)


def make_player(options=None, **kwargs):
    events: list[dict] = []
    fakes: list[FakeMPV] = []

    def factory(**kw):
        fake = FakeMPV(**kw)
        fakes.append(fake)
        return fake

    player = NativePlayer(
        events.append, options=options or PlayerOptions(), mpv_factory=factory, **kwargs
    )
    return player, events, fakes


def wait_for(events, kind, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = [e for e in events if e.get("type") == kind]
        if found:
            return found
        time.sleep(0.01)
    return []


# ── Options ──────────────────────────────────────────────────────────────────


def test_options_reject_invalid_values():
    o = PlayerOptions.from_dict(
        {
            "engine": "vlc",
            "spdif": ["ac3", "mp3", "truehd"],
            "replaygain": "loud",
            "replaygain_preamp": 99,
            "audio_delay_ms": -9000,
            "eq_gains": [30, "x", 0, 0, 0, 0, 0, 0, 0, float("nan")],
            "channels": "9.2",
            "exclusive": "yes",
            "audio_device": "  ",
        }
    )
    assert o.engine == "auto"
    assert o.spdif == ["ac3", "truehd"]
    assert o.replaygain == "no"
    assert o.replaygain_preamp == 15.0
    assert o.audio_delay_ms == -2000
    assert o.eq_gains[0] == 12.0 and o.eq_gains[1] == 0.0 and o.eq_gains[9] == 0.0
    assert o.channels == "auto"
    assert o.exclusive is False
    assert o.audio_device == "auto"
    assert PlayerOptions.from_dict("garbage") == PlayerOptions()


def test_options_round_trip_through_the_file():
    o = PlayerOptions(spdif=["ac3", "dts"], exclusive=True, audio_device="wasapi/{x}")
    np_mod.save_options(o)
    assert np_mod.load_options() == o


def test_mpv_properties_follow_the_options():
    props = mpv_properties(
        PlayerOptions(
            spdif=["ac3", "dts"],
            exclusive=True,
            gapless=False,
            replaygain="album",
            audio_delay_ms=150,
            channels="5.1",
        )
    )
    assert props["audio-spdif"] == "ac3,dts"
    assert props["audio-exclusive"] is True
    assert props["gapless-audio"] == "no"
    assert props["replaygain"] == "album"
    assert props["audio-delay"] == pytest.approx(0.15)
    assert props["audio-channels"] == "5.1"
    assert mpv_properties(PlayerOptions())["audio-channels"] == "auto-safe"


def test_filter_chain_equalizer_with_headroom_upmix_and_normalizer():
    assert filter_chain(PlayerOptions()) == []
    assert filter_chain(PlayerOptions(eq_enabled=True)) == []  # flat: no filter
    chain = filter_chain(
        PlayerOptions(
            eq_enabled=True,
            eq_gains=list(EQ_PRESETS["rock"]),
            channels="5.1",
            upmix=True,
            normalize="dynaudnorm",
        )
    )
    assert len(chain) == 3
    eq = chain[0]
    assert eq.startswith("lavfi=[volume=-4dB,equalizer=f=31:t=o:w=1:g=4")
    assert "f=250:" not in eq  # a 0 dB band is left out
    assert chain[1] == "lavfi=[surround=chl_out=5.1]"
    assert chain[2].startswith("lavfi=[dynaudnorm")
    # Upmix only toward a surround layout.
    assert filter_chain(PlayerOptions(upmix=True, channels="stereo")) == []


def test_bitstream_codecs():
    assert is_bitstreamed("ac3", ["ac3"])
    assert is_bitstreamed("dts", ["dts-hd"])
    assert not is_bitstreamed("eac3", ["ac3"])
    assert not is_bitstreamed("flac", ["ac3", "dts"])
    assert not is_bitstreamed(None, ["ac3"])


def test_output_label():
    assert (
        output_label("ac3", {"hr-channels": "5.1"}, {"format": "spdif-ac3"})
        == "Bitstream AC-3 5.1"
    )
    assert (
        output_label(
            "flac", {}, {"format": "s32", "samplerate": 96000, "hr-channels": "stereo"}
        )
        == "PCM 32-bit / 96 kHz stereo"
    )
    assert output_label("flac", {}, {"format": "floatp", "samplerate": 44100}) == (
        "PCM 32-bit float / 44.1 kHz"
    )
    assert output_label(None, None, None) == ""


# ── The player, on a fake mpv ────────────────────────────────────────────────


def test_mpv_is_created_on_first_use_with_options_applied():
    player, _events, fakes = make_player(PlayerOptions(spdif=["ac3"], exclusive=True))
    assert fakes == []
    player.load("/music/a.flac")
    (fake,) = fakes
    assert fake.kwargs["video"] is False
    assert fake.kwargs["media_controls"] == "yes"
    assert fake.props["audio-spdif"] == "ac3"
    assert fake.props["audio-exclusive"] is True
    assert fake.props["af"] == ""
    assert ("loadfile", "/music/a.flac", "replace") in fake.commands
    player.close()
    assert fake.terminated


def test_an_mpv_without_media_controls_still_starts():
    made = []

    def factory(**kw):
        if "media_controls" in kw:
            raise AttributeError("no such option")
        made.append(kw)
        return FakeMPV(**kw)

    player = NativePlayer(lambda e: None, options=PlayerOptions(), mpv_factory=factory)
    player.load("/music/a.flac")
    assert made and "media_controls" not in made[0]
    player.close()


def test_mpv_that_cannot_start_is_reported():
    def factory(**kw):
        raise OSError("boom")

    player = NativePlayer(lambda e: None, options=PlayerOptions(), mpv_factory=factory)
    with pytest.raises(NativePlayerUnavailable):
        player.load("/music/a.flac")


def test_next_file_is_queued_and_replaced():
    player, _events, fakes = make_player()
    player.load("/m/a.flac", "/m/b.flac")
    fake = fakes[0]
    assert fake.commands[-1] == ("loadfile", "/m/b.flac", "append")
    fake.commands.clear()
    player.set_next("/m/b.flac")  # unchanged: nothing sent
    assert fake.commands == []
    player.set_next("/m/c.flac")
    assert fake.commands == [("playlist-clear",), ("loadfile", "/m/c.flac", "append")]
    player.set_next(None)
    assert fake.commands[-1] == ("playlist-clear",)
    player.close()


def test_gapless_advance_and_end_of_queue_events():
    player, events, fakes = make_player()
    player.load("/m/a.flac", "/m/b.flac")
    fake = fakes[0]
    fake.event(event="end-file", reason="eof")
    fake.event(event="file-loaded")
    assert wait_for(events, "advanced") == [{"type": "advanced", "path": "/m/b.flac"}]
    assert not [e for e in events if e["type"] == "ended"]
    fake.event(event="end-file", reason="eof")  # nothing queued after b
    assert wait_for(events, "ended")
    fake.event(event="end-file", reason="error", file_error="unrecognized file format")
    assert wait_for(events, "error")[0]["message"] == "unrecognized file format"
    player.close()


def test_a_load_after_a_natural_end_is_not_an_advance():
    player, events, fakes = make_player()
    player.load("/m/a.flac", "/m/b.flac")
    fake = fakes[0]
    fake.event(event="end-file", reason="eof")
    player.load("/m/z.flac")  # the user picked another track meanwhile
    fake.event(event="file-loaded")
    time.sleep(0.1)
    assert not wait_for(events, "advanced", 0.3)
    player.close()


def test_time_updates_are_throttled_but_the_last_one_arrives():
    player, events, fakes = make_player()
    player.load("/m/a.flac")
    fake = fakes[0]
    for i in range(50):
        fake.change("time-pos", i / 10)
    time.sleep(np_mod.TIME_UPDATE_INTERVAL_S * 3)
    times = [e["time"] for e in events if e["type"] == "timeupdate"]
    assert 1 <= len(times) <= 3
    assert times[-1] == pytest.approx(4.9)
    player.close()


def test_pause_and_metadata_events():
    player, events, fakes = make_player()
    player.load("/m/a.flac")
    fake = fakes[0]
    fake.change("pause", False)  # already playing: no event
    fake.change("pause", True)
    fake.change("duration", 200.0)
    fake.event(event="playback-restart")
    wait_for(events, "seeked")
    kinds = [e["type"] for e in events]
    assert kinds.count("play") == 0
    assert "pause" in kinds and "loadedmetadata" in kinds and "seeked" in kinds
    player.close()


def test_filters_are_dropped_for_a_bitstreamed_codec():
    opts = PlayerOptions(
        spdif=["ac3"], eq_enabled=True, eq_gains=list(EQ_PRESETS["bass"])
    )
    player, events, fakes = make_player(opts)
    player.load("/m/a.ac3")
    fake = fakes[0]
    assert fake.props["af"].startswith("lavfi=[")  # codec not known yet
    fake.change("audio-codec-name", "ac3")
    assert fake.props["af"] == ""
    fake.change("audio-codec-name", "flac")
    assert fake.props["af"].startswith("lavfi=[")
    fake.change("audio-params", {"hr-channels": "5.1"})
    fake.change("audio-codec-name", "ac3")
    fake.change("audio-out-params", {"format": "spdif-ac3"})
    end = time.monotonic() + 2
    while time.monotonic() < end and not any(
        e.get("label") == "Bitstream AC-3 5.1" for e in events
    ):
        time.sleep(0.01)
    last = [e for e in events if e["type"] == "output"][-1]
    assert last["label"] == "Bitstream AC-3 5.1"
    assert last["bitstream"] is True
    player.close()


def test_commands():
    player, _events, fakes = make_player()
    player.load("/m/a.flac")
    fake = fakes[0]
    player.command("pause")
    assert fake.pause is True
    player.command("play")
    assert fake.pause is False
    player.command("seek", 12.5)
    assert fake.commands[-1] == ("seek", 12.5, "absolute", "exact")
    player.command("volume", 0.4)
    assert fake.volume == pytest.approx(40.0)
    player.command("volume", 7)
    assert fake.volume == 100.0
    player.command("speed", 9)
    assert fake.speed == 4.0
    player.command("ab_loop", [10, 20])
    assert (fake.props["ab-loop-a"], fake.props["ab-loop-b"]) == (10.0, 20.0)
    player.command("ab_loop", None)
    assert (fake.props["ab-loop-a"], fake.props["ab-loop-b"]) == ("no", "no")
    with pytest.raises(ValueError):
        player.command("format_disk")
    player.close()


def test_set_options_saves_applies_and_loads_presets():
    player, _events, fakes = make_player()
    player.load("/m/a.flac")
    fake = fakes[0]
    o = player.set_options(
        {"eq_enabled": True, "eq_preset": "treble", "replaygain": "track"}
    )
    assert o.eq_gains == [float(g) for g in EQ_PRESETS["treble"]]
    assert fake.props["replaygain"] == "track"
    assert fake.props["af"].startswith("lavfi=[")
    assert np_mod.load_options() == o
    # Gains sent with the preset win (the user moved a slider).
    o = player.set_options({"eq_preset": "custom", "eq_gains": [1] * 10})
    assert o.eq_gains == [1.0] * 10
    player.close()


def test_switching_bitstream_mid_song_reopens_the_file_where_it_was():
    player, events, fakes = make_player()
    player.load("/m/a.ac3", "/m/b.flac")
    fake = fakes[0]
    fake.change("time-pos", 42.0)
    fake.commands.clear()
    player.set_options({"spdif": ["ac3"]})
    assert fake.props["audio-spdif"] == "ac3"
    assert fake.commands == [
        ("loadfile", "/m/a.ac3", "replace"),
        ("playlist-clear",),
        ("loadfile", "/m/b.flac", "append"),
    ]
    assert fake.pause is True  # held while it reopens
    fake.commands.clear()
    fake.event(event="file-loaded")
    end = time.monotonic() + 2
    while time.monotonic() < end and fake.pause:
        time.sleep(0.01)
    assert fake.commands == [("seek", 42.0, "absolute", "exact")]
    assert fake.pause is False
    assert not wait_for(events, "advanced", 0.2)
    assert not [e for e in events if e["type"] in ("pause", "error")]
    player.close()


def test_options_that_keep_the_output_do_not_reopen():
    player, _events, fakes = make_player()
    player.load("/m/a.flac")
    fake = fakes[0]
    fake.commands.clear()
    player.set_options({"replaygain": "album", "eq_enabled": True})
    assert not [c for c in fake.commands if c[0] == "loadfile"]
    player.close()


def test_a_refused_bitstream_falls_back_to_pcm_instead_of_stopping():
    player, events, fakes = make_player(PlayerOptions(spdif=["ac3"]))
    player.load("/m/a.ac3")
    fake = fakes[0]
    fake.change("time-pos", 10.0)
    fake.commands.clear()
    fake.event(event="end-file", reason="error", file_error="audio output failed")
    warning = wait_for(events, "warning")
    assert warning and "PCM" in warning[0]["message"]
    end = time.monotonic() + 2
    while time.monotonic() < end and not fake.commands:
        time.sleep(0.01)
    assert fake.props["audio-spdif"] == ""
    assert fake.commands[0] == ("loadfile", "/m/a.ac3", "replace")
    assert not [e for e in events if e["type"] == "error"]
    # A second failure is a real error.
    fake.event(event="end-file", reason="error", file_error="still broken")
    assert wait_for(events, "error")[0]["message"] == "still broken"
    # Choosing bitstream again tries it again.
    player.set_options({"spdif": ["ac3", "eac3"]})
    assert fake.props["audio-spdif"] == "ac3,eac3"
    player.close()


def test_set_options_without_mpv_only_stores():
    player, _events, fakes = make_player()
    player.set_options({"engine": "browser"})
    assert fakes == []
    assert np_mod.load_options().engine == "browser"


# ── Loading libmpv ───────────────────────────────────────────────────────────


def test_missing_python_mpv_is_reported(monkeypatch):
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mpv":
            raise ImportError("no mpv")
        return real_import(name, *a, **k)

    np_mod._reset_loader_for_tests()
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "mpv", raising=False)
    try:
        with pytest.raises(NativePlayerUnavailable, match="python-mpv"):
            np_mod.load_mpv()
        # The failure is remembered: no second import attempt.
        with pytest.raises(NativePlayerUnavailable):
            np_mod.load_mpv()
    finally:
        np_mod._reset_loader_for_tests()


# ── The API mixin ────────────────────────────────────────────────────────────


def _api(window=True, approved=lambda p: True):
    from mornye.api_mixins.native_player import NativePlayerMixin

    class Api(NativePlayerMixin):
        def __init__(self):
            self._window = object() if window else None
            self._native_player = None
            self.pushed: list = []

        def _is_approved_path(self, p):
            return approved(p)

        def _push(self, fn, *args):
            self.pushed.append((fn, args))

    return Api()


def _fake_backed(api, fakes):
    def factory(**kw):
        fakes.append(FakeMPV(**kw))
        return fakes[-1]

    api._native_player = NativePlayer(
        lambda e: api._push("app_native_player_event", e),
        options=PlayerOptions(),
        mpv_factory=factory,
    )


def test_api_is_desktop_only():
    api = _api(window=False)
    assert api.native_player_status()["available"] is False
    assert api.native_player_load("/m/a.flac")["status"] == "unavailable"
    assert (
        api.native_player_set_options({"engine": "native"})["status"] == "unavailable"
    )


def test_api_refuses_unapproved_paths_and_unknown_commands():
    api = _api(approved=lambda p: p.startswith("/ok/"))
    fakes: list[FakeMPV] = []
    _fake_backed(api, fakes)
    assert api.native_player_load("/etc/passwd")["status"] == "error"
    assert fakes == []
    assert api.native_player_load("/ok/a.flac", "/etc/b.flac")["status"] == "ok"
    assert ("loadfile", "/ok/a.flac", "replace") in fakes[0].commands
    assert not any("/etc/b.flac" in c for c in fakes[0].commands)
    assert api.native_player_set_next("/etc/c.flac")["status"] == "ok"
    assert not any("/etc/c.flac" in c for c in fakes[0].commands)
    assert api.native_player_command("rm")["status"] == "error"
    assert api.native_player_command("seek", 3)["status"] == "ok"
    assert api.native_player_devices()["devices"] == [
        {"name": "auto", "description": "Auto"}
    ]
    api._close_native_player()
    assert fakes[0].terminated


def test_events_reach_the_page_function():
    api = _api()
    fakes: list[FakeMPV] = []
    _fake_backed(api, fakes)
    api.native_player_load("/ok/a.flac")
    fakes[0].event(event="end-file", reason="eof")
    end = time.monotonic() + 2
    while time.monotonic() < end and not api.pushed:
        time.sleep(0.01)
    assert api.pushed[0] == ("app_native_player_event", ({"type": "ended"},))
    api._close_native_player()


def test_native_methods_are_not_reachable_over_http():
    from mornye.webapp import ALLOWED_METHODS

    assert not [m for m in ALLOWED_METHODS if m.startswith("native_player")]


def test_prepare_playback_native_makes_no_copy(tmp_path, monkeypatch):
    from mornye.app import MornyeAPI

    src = tmp_path / "a.ape"
    src.write_bytes(b"not really audio")
    api = MornyeAPI()
    monkeypatch.setattr(api, "_is_approved_path", lambda p: True)
    called = []
    monkeypatch.setattr("mornye.core.playback.playable_file_async", called.append)
    res = api.prepare_playback(str(src), True, True)
    assert res["status"] == "ok"
    assert (res["path"], res["url"], res["mode"]) == (str(src), "", "original")
    assert called == []


# ── Real libmpv (skipped when it is not installed) ───────────────────────────


def _real_mpv():
    try:
        return np_mod.load_mpv()
    except NativePlayerUnavailable:
        return None


needs_libmpv = pytest.mark.skipif(
    _real_mpv() is None or shutil.which("ffmpeg") is None,
    reason="python-mpv/libmpv or ffmpeg not installed",
)


def _tone(path: Path, freq: int, seconds: int = 3) -> str:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}",
            str(path),
        ],
        check=True,
    )
    return str(path)


def _real_player(events, options=None):
    mpv = _real_mpv()
    return NativePlayer(
        events.append,
        options=options or PlayerOptions(),
        mpv_factory=lambda **kw: mpv.MPV(ao="null", **kw),
        media_controls=False,
    )


@needs_libmpv
def test_real_mpv_plays_gapless_and_ends(tmp_path):
    a = _tone(tmp_path / "a.flac", 440)
    b = _tone(tmp_path / "b.flac", 660)
    events: list[dict] = []
    player = _real_player(
        events, PlayerOptions(eq_enabled=True, eq_gains=list(EQ_PRESETS["rock"]))
    )
    try:
        player.load(a, b)
        meta = wait_for(events, "loadedmetadata", 5)
        assert meta and meta[0]["duration"] == pytest.approx(3, abs=0.1)
        player.command("seek", 2.6)
        assert wait_for(events, "advanced", 5) == [{"type": "advanced", "path": b}]
        end = time.monotonic() + 3  # the decoder opens just after the change
        while time.monotonic() < end and not player.stats()["codec"]:
            time.sleep(0.02)
        stats = player.stats()
        assert stats["path"] == b
        assert stats["codec"] == "flac"
        assert stats["filters"] == ["lavfi"]
        assert player.devices()[0]["name"] == "auto"
        player.command("seek", 2.6)
        assert wait_for(events, "ended", 5)
    finally:
        player.close()


@needs_libmpv
def test_real_mpv_accepts_every_filter(tmp_path):
    a = _tone(tmp_path / "a.flac", 440)
    events: list[dict] = []
    player = _real_player(events)
    try:
        player.load(a)
        wait_for(events, "loadedmetadata", 5)
        for normalize in ("dynaudnorm", "loudnorm"):
            player.set_options(
                {
                    "eq_enabled": True,
                    "eq_preset": "loudness",
                    "channels": "7.1",
                    "upmix": True,
                    "normalize": normalize,
                }
            )
            assert player.stats()["filters"] == ["lavfi", "lavfi", "lavfi"]
        assert not [e for e in events if e["type"] == "warning"]
    finally:
        player.close()


@needs_libmpv
def test_real_mpv_reports_a_broken_file(tmp_path):
    bad = tmp_path / "bad.flac"
    bad.write_bytes(b"\x00" * 1000)
    events: list[dict] = []
    player = _real_player(events)
    try:
        player.load(str(bad))
        assert wait_for(events, "error", 5)
    finally:
        player.close()
