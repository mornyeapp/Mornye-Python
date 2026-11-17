"""``probe_audio_quality``: reads a downloaded file's real format.

The album card no longer shows a quality tag; the API stays for callers
that want to know what a file really is.
"""

from __future__ import annotations

import wave
from pathlib import Path

from mornye.api_mixins.library_tools import LibraryToolsMixin
from mornye.webapp import ALLOWED_METHODS


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
