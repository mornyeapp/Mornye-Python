"""core/scrobbler.py — Last.fm scrobbling, with a stand-in for its API."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import time

import pytest

from mornye.core import scrobbler as sc


@pytest.fixture(autouse=True)
def _isolated_files(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_config_file", lambda: tmp_path / "lastfm.json")
    monkeypatch.setattr(sc, "_queue_file", lambda: tmp_path / "scrobble-queue.json")
    monkeypatch.setattr(sc, "AUTH_POLL_SECONDS", 0.0)


class FakeLastFm:
    """Answers like ws.audioscrobbler.com and keeps every call."""

    def __init__(self):
        self.calls: list[dict] = []
        self.offline = False
        self.scrobble_error: int | None = None
        self.approve_after = 0

    def __call__(self, body: dict):
        self.calls.append(body)
        if self.offline:
            raise OSError("no network")
        method = body["method"]
        if method == "auth.getToken":
            return {"token": "tok"}
        if method == "auth.getSession":
            self.approve_after -= 1
            if self.approve_after >= 0:
                return {"error": 14, "message": "This token has not been authorized"}
            return {"session": {"name": "ada", "key": "sk-1"}}
        if method == "track.scrobble" and self.scrobble_error is not None:
            return {"error": self.scrobble_error, "message": "nope"}
        return {method: {}}

    def methods(self) -> list[str]:
        return [c["method"] for c in self.calls]


def _connected(api: FakeLastFm) -> sc.LastFm:
    sc.save_config(
        sc.LastFmConfig(
            enabled=True, api_key="key", secret="secret", session="sk", user="ada"
        )
    )
    return sc.LastFm(http=api)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _wait_idle(lastfm: sc.LastFm) -> None:
    assert _wait(lambda: not lastfm._sending)


HEARD = {"title": "Song", "artist": "Artist", "album": "Record", "outcome": "listened"}


def test_signature_is_md5_of_sorted_params_and_secret() -> None:
    params = {"method": "auth.getToken", "api_key": "k", "format": "json"}
    expected = hashlib.md5(b"api_keykmethodauth.getTokens3cret").hexdigest()
    assert sc.sign(params, "s3cret") == expected


@pytest.mark.parametrize(
    ("heard", "duration", "expected"),
    [
        (100, 200, True),  # half
        (99, 200, False),
        (240, 1200, True),  # four minutes of a long one
        (239, 1200, False),
        (29, 29, False),  # too short a song
        (240, 0, True),  # unknown length: only four minutes is sure
        (200, 0, False),
    ],
)
def test_lastfms_rule(heard, duration, expected) -> None:
    assert sc.should_scrobble(heard, duration) is expected


def test_a_heard_song_is_scrobbled_with_its_start_time() -> None:
    api = FakeLastFm()
    lastfm = _connected(api)
    assert lastfm.listened(
        {**HEARD, "heard": 120, "duration": 200, "started": 1_700_000_000}
    )
    _wait_idle(lastfm)
    scrobble = [c for c in api.calls if c["method"] == "track.scrobble"][-1]
    assert scrobble["artist"] == "Artist" and scrobble["track"] == "Song"
    assert scrobble["timestamp"] == "1700000000" and scrobble["duration"] == "200"
    assert scrobble["sk"] == "sk" and scrobble["api_sig"]
    assert lastfm.status()["pending"] == 0


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("Drake;Lil Durk", "Drake"),
        ("Travis Scott; The 1975; Big Sean", "Travis Scott"),
        ("Simon & Garfunkel", "Simon & Garfunkel"),
        ("Tyler, The Creator", "Tyler, The Creator"),
        (";The Weeknd", "The Weeknd"),
        ("A\x00B", "A"),
    ],
)
def test_only_the_main_artist_of_a_multi_value_tag_goes_to_lastfm(
    tag, expected
) -> None:
    assert sc.lastfm_artist(tag) == expected


def test_a_multi_artist_song_is_scrobbled_under_its_main_artist() -> None:
    api = FakeLastFm()
    lastfm = _connected(api)
    assert lastfm.listened(
        {**HEARD, "artist": "Drake;Lil Durk", "heard": 120, "duration": 200}
    )
    _wait_idle(lastfm)
    scrobble = [c for c in api.calls if c["method"] == "track.scrobble"][-1]
    assert scrobble["artist"] == "Drake"


def test_a_skipped_song_is_not_scrobbled() -> None:
    api = FakeLastFm()
    lastfm = _connected(api)
    assert not lastfm.listened({**HEARD, "heard": 20, "duration": 200})
    assert "track.scrobble" not in api.methods()


def test_nothing_is_kept_while_switched_off() -> None:
    sc.save_config(
        sc.LastFmConfig(enabled=False, api_key="k", secret="s", session="sk")
    )
    lastfm = sc.LastFm(http=FakeLastFm())
    assert not lastfm.listened({**HEARD, "heard": 300, "duration": 300})
    assert lastfm.status()["pending"] == 0


def test_offline_scrobbles_wait_and_survive_a_restart(monkeypatch) -> None:
    monkeypatch.setattr(sc, "RETRY_SECONDS", 3600)
    api = FakeLastFm()
    api.offline = True
    lastfm = _connected(api)
    lastfm.listened({**HEARD, "heard": 300, "duration": 300})
    _wait_idle(lastfm)
    assert lastfm.status()["pending"] == 1

    api.offline = False
    again = sc.LastFm(http=api)
    assert again.status()["pending"] == 1
    again.flush(wait=True)
    _wait_idle(again)
    assert again.status()["pending"] == 0


def test_a_refused_scrobble_is_dropped_and_a_temporary_one_kept(monkeypatch) -> None:
    monkeypatch.setattr(sc, "RETRY_SECONDS", 3600)
    api = FakeLastFm()
    lastfm = _connected(api)
    api.scrobble_error = 6  # invalid parameters: will never be accepted
    lastfm.listened({**HEARD, "heard": 300, "duration": 300})
    _wait_idle(lastfm)
    assert lastfm.status()["pending"] == 0

    api.scrobble_error = 29  # rate limited
    lastfm.listened({**HEARD, "heard": 300, "duration": 300})
    _wait_idle(lastfm)
    assert lastfm.status()["pending"] == 1


def test_an_invalid_session_asks_to_connect_again() -> None:
    api = FakeLastFm()
    lastfm = _connected(api)
    api.scrobble_error = 9
    lastfm.listened({**HEARD, "heard": 300, "duration": 300})
    _wait_idle(lastfm)
    status = lastfm.status()
    assert not status["connected"]
    assert status["status_text"] == "Connect Last.fm again"
    assert status["pending"] == 1


def test_signing_in_waits_for_approval_and_keeps_the_session() -> None:
    api = FakeLastFm()
    api.approve_after = 2
    sc.save_config(sc.LastFmConfig(enabled=True, api_key="key", secret="secret"))
    lastfm = sc.LastFm(http=api)
    url = lastfm.begin_auth()
    assert url == "https://www.last.fm/api/auth/?api_key=key&token=tok"
    assert _wait(lambda: lastfm.status()["connected"])
    assert _wait(lambda: not lastfm.status()["authorising"])
    assert lastfm.status()["status_text"] == "Connected as ada"
    assert sc.load_config().session == "sk-1"


def test_settings_file_is_private_and_a_new_key_signs_out(tmp_path) -> None:
    lastfm = _connected(FakeLastFm())
    lastfm.configure(api_key="other")
    cfg = sc.load_config()
    assert cfg.api_key == "other" and cfg.session == "" and cfg.secret == "secret"
    if sys.platform != "win32":
        mode = stat.S_IMODE((tmp_path / "lastfm.json").stat().st_mode)
        assert mode == 0o600
    # Neither the secret nor the session key is handed back to the page.
    shown = json.dumps(lastfm.status())
    assert '"secret"' not in shown and "sk" not in json.loads(shown).values()


def test_now_playing_is_sent_once_per_song() -> None:
    api = FakeLastFm()
    lastfm = _connected(api)
    playing = {
        "title": "Song",
        "artist": "Artist",
        "album": "Record",
        "duration": 200,
        "playing": True,
    }
    lastfm.now_playing(playing)
    lastfm.now_playing({**playing, "position": 30})
    assert _wait(lambda: api.methods().count("track.updateNowPlaying") == 1)
    time.sleep(0.1)
    assert api.methods().count("track.updateNowPlaying") == 1


def test_web_mode_listening_never_scrobbles(monkeypatch) -> None:
    from mornye.api_mixins.personal import PersonalMixin

    seen = []
    monkeypatch.setattr(
        sc, "service", lambda: type("S", (), {"listened": seen.append})()
    )

    class Api(PersonalMixin):
        _ws_broadcast = None

    entry = {**HEARD, "ms": 1000, "heard": 300, "duration": 300}
    Api().record_listening(entry)
    assert len(seen) == 1

    web = Api()
    web._ws_broadcast = lambda *a: None
    web.record_listening(entry)
    assert len(seen) == 1
