"""The optional Dolby Digital (AC-3) copy of Dolby Digital Plus downloads,
for receivers on S/PDIF (coaxial, or optical from a TV)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mornye.application.post_processing import apply_post_processing
from mornye.core.models import DownloadResult, TrackMetadata
from mornye.core.transcode import convert_dolby_to_ac3_async, dolby_ac3_path

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _source(path: Path, codec: str, layout: str = "5.1") -> Path:
    pan = {
        "5.1": "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0",
        "stereo": "pan=stereo|c0=c0|c1=c0",
    }[layout]
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1",
        "-af",
        pan,
        "-c:a",
        codec,
        "-metadata",
        "title=Underneath the Stars",
        "-metadata",
        "artist=Mariah Carey",
        "-f",
        "mp4",
        str(path),
    )
    return path


def _stream(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,channels,bit_rate:format_tags=title,artist",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    stream["tags"] = {k.lower(): v for k, v in data["format"].get("tags", {}).items()}
    return stream


def test_eac3_51_becomes_ac3_51_beside_the_original(tmp_path) -> None:
    src = _source(tmp_path / "song.m4a", "eac3")

    out = asyncio.run(convert_dolby_to_ac3_async(src))

    assert out == tmp_path / "song (AC-3).m4a"
    assert src.exists()  # kept by default
    info = _stream(out)
    assert info["codec_name"] == "ac3"
    assert info["channels"] == 6
    assert int(info["bit_rate"]) >= 600_000
    assert info["tags"]["title"] == "Underneath the Stars"
    assert info["tags"]["artist"] == "Mariah Carey"
    assert not list(tmp_path.glob(".*mornye-ac3*"))


def test_replacing_the_original_keeps_its_name(tmp_path) -> None:
    src = _source(tmp_path / "song.m4a", "eac3")

    out = asyncio.run(convert_dolby_to_ac3_async(src, keep_original=False))

    assert out == src == dolby_ac3_path(src, keep_original=False)
    assert _stream(out)["codec_name"] == "ac3"
    assert not (tmp_path / "song (AC-3).m4a").exists()


def test_stereo_dolby_digital_plus_stays_stereo(tmp_path) -> None:
    src = _source(tmp_path / "song.m4a", "eac3", layout="stereo")

    out = asyncio.run(convert_dolby_to_ac3_async(src))

    assert out is not None
    assert _stream(out)["channels"] == 2


@pytest.mark.parametrize("codec", ["aac", "ac3"])
def test_anything_but_eac3_is_left_alone(tmp_path, codec) -> None:
    src = _source(tmp_path / "song.m4a", codec)
    before = src.read_bytes()

    assert asyncio.run(convert_dolby_to_ac3_async(src)) is None
    assert src.read_bytes() == before
    assert list(tmp_path.iterdir()) == [src]


def _options(**overrides) -> SimpleNamespace:
    base = {
        "transcode_to": None,
        "dolby_to_ac3": False,
        "dolby_ac3_keep_original": True,
        "save_lrc": False,
        "lrc_library_dir": None,
        "save_canvas": False,
        "canvas_library_dir": None,
        "save_animated_artwork": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _metadata() -> TrackMetadata:
    return TrackMetadata(
        id="1",
        title="Underneath the Stars",
        artists="Mariah Carey",
        album="Daydream",
        album_artist="Mariah Carey",
    )


def test_the_copy_is_on_by_default() -> None:
    from mornye.application.download_engine import DownloadOptions
    from mornye.core.profiles import ProfileConfig
    from mornye.tui.config_state import ConfigState

    assert DownloadOptions(output_dir=".").dolby_to_ac3 is True
    assert DownloadOptions(output_dir=".").dolby_ac3_keep_original is True
    assert ProfileConfig().dolby_to_ac3 is True
    assert ConfigState().dolby_to_ac3 is True


def test_post_processing_does_nothing_when_switched_off(tmp_path) -> None:
    src = _source(tmp_path / "song.m4a", "eac3")
    result = DownloadResult.ok("ext:tidal-web", str(src), "m4a")

    out = asyncio.run(
        apply_post_processing(result, _metadata(), _options(dolby_to_ac3=False))
    )

    assert out.file_path == str(src)
    assert list(tmp_path.iterdir()) == [src]


def test_post_processing_points_the_result_at_the_ac3_copy(tmp_path) -> None:
    src = _source(tmp_path / "song.m4a", "eac3")
    result = DownloadResult.ok("ext:tidal-web", str(src), "m4a")

    out = asyncio.run(
        apply_post_processing(result, _metadata(), _options(dolby_to_ac3=True))
    )

    assert out.success
    assert out.file_path == str(tmp_path / "song (AC-3).m4a")
    assert src.exists()
