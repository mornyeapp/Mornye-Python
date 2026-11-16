"""The album card's quality readout (plan §2, signature 1; §0 item 7).

The card shows the tier asked for in Settings until a track of it has
downloaded; then ``probe_audio_quality`` reads that file.
"""

from __future__ import annotations

import wave
from pathlib import Path

from mornye.api_mixins.library_tools import LibraryToolsMixin
from mornye.webapp import ALLOWED_METHODS

APP_JS = (
    Path(__file__).resolve().parents[1] / "mornye" / "frontend" / "app.js"
).read_text(encoding="utf-8")


class _Api(LibraryToolsMixin):
    def __init__(self, download_dir: str) -> None:
        self.download_dir = download_dir


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0\0" * 2 * 4410)
    return path


def test_probe_reads_a_downloaded_file(tmp_path):
    res = _Api(str(tmp_path)).probe_audio_quality(str(_wav(tmp_path / "a.wav")))
    assert res["status"] == "ok"
    assert res["info"]["sample_rate"] == 44100
    assert res["info"]["format"] == "WAV"


def test_probe_refuses_paths_outside_the_approved_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    api = _Api(str(tmp_path / "music"))
    assert api.probe_audio_quality("/etc/hosts")["status"] == "error"


def test_probe_is_reachable_from_the_web_ui():
    assert "probe_audio_quality" in ALLOWED_METHODS


def test_frontend_shows_requested_then_real_quality():
    assert "showRequestedAlbumQuality();" in APP_JS
    assert "updateAlbumQualityFromStats(data.queue)" in APP_JS
    assert "REQ · " in APP_JS
