"""MP3 from Hi-Res: resampled within the rate family, with SoXR when available."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from mornye.core import transcode


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (0, None),
        (44_100, None),
        (48_000, None),
        (88_200, 44_100),
        (176_400, 44_100),
        (96_000, 48_000),
        (192_000, 48_000),
        (352_800, 44_100),
    ],
)
def test_target_rate(source, target):
    assert transcode.mp3_target_sample_rate(source) == target


def _cmd(monkeypatch, *, soxr: bool, rate: int, fmt=transcode.MP3):
    monkeypatch.setattr(transcode, "soxr_available", lambda: soxr)
    return transcode._encode_command(
        transcode.format_spec(fmt),
        Path("in.flac"),
        Path("out"),
        "320k",
        24,
        sample_rate=rate,
    )


def test_soxr_used_when_built_in(monkeypatch):
    cmd = _cmd(monkeypatch, soxr=True, rate=88_200)
    assert cmd[cmd.index("-af") + 1] == "aresample=44100:resampler=soxr:precision=28"
    assert cmd[cmd.index("-ar") + 1] == "44100"


def test_plain_rate_without_soxr(monkeypatch):
    cmd = _cmd(monkeypatch, soxr=False, rate=96_000)
    assert "-af" not in cmd
    assert cmd[cmd.index("-ar") + 1] == "48000"


def test_cd_rate_and_lossless_targets_untouched(monkeypatch):
    assert "-ar" not in _cmd(monkeypatch, soxr=True, rate=44_100)
    flac = _cmd(monkeypatch, soxr=True, rate=96_000, fmt=transcode.FLAC)
    assert "-ar" not in flac and "-af" not in flac


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_88k_source_becomes_44k_mp3(tmp_path):
    src = tmp_path / "hi.flac"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1:sample_rate=88200",
            "-c:a",
            "flac",
            str(src),
        ],
        check=True,
    )
    out = asyncio.run(transcode.transcode_file_async(src, keep_original=True))

    from mutagen.mp3 import MP3

    assert MP3(str(out)).info.sample_rate == 44_100


# ── Caps on the converted file, AAC and Opus ─────────────────────────────


@pytest.mark.parametrize(
    ("fmt", "rate", "depth", "caps", "expected"),
    [
        (transcode.FLAC, 96_000, 24, {}, (None, None)),
        (transcode.FLAC, 96_000, 24, {"max_sample_rate": 48_000}, (48_000, None)),
        (transcode.FLAC, 176_400, 24, {"max_sample_rate": 96_000}, (88_200, None)),
        (transcode.FLAC, 96_000, 24, {"max_sample_rate": 44_100}, (44_100, None)),
        (transcode.FLAC, 44_100, 24, {"max_bit_depth": 16}, (None, 16)),
        (transcode.FLAC, 44_100, 16, {"max_bit_depth": 16}, (None, None)),
        (transcode.MP3, 96_000, 24, {"max_bit_depth": 16}, (48_000, None)),
        (transcode.MP3, 96_000, 24, {"max_sample_rate": 44_100}, (44_100, None)),
        (transcode.AAC, 192_000, 24, {}, (48_000, None)),
        (transcode.OPUS, 44_100, 16, {}, (48_000, None)),
        (transcode.OPUS, 48_000, 16, {}, (None, None)),
    ],
)
def test_plan_resample(fmt, rate, depth, caps, expected):
    spec = transcode.format_spec(fmt)
    assert transcode.plan_resample(spec, rate, depth, **caps) == expected


def test_depth_cap_dithers_and_sets_sample_format(monkeypatch):
    monkeypatch.setattr(transcode, "soxr_available", lambda: False)
    cmd = transcode._encode_command(
        transcode.format_spec(transcode.FLAC),
        Path("in.flac"),
        Path("out"),
        "320k",
        24,
        sample_rate=96_000,
        max_bit_depth=16,
    )
    assert cmd[cmd.index("-af") + 1] == "aresample=dither_method=triangular"
    assert cmd[cmd.index("-sample_fmt") + 1] == "s16"
    assert "-ar" not in cmd


def test_opus_bitrate_is_clamped(monkeypatch):
    monkeypatch.setattr(transcode, "soxr_available", lambda: False)
    cmd = transcode._encode_command(
        transcode.format_spec(transcode.OPUS),
        Path("in.flac"),
        Path("out"),
        "320k",
        16,
        sample_rate=48_000,
    )
    assert cmd[cmd.index("-b:a") + 1] == "256k"


def _sine(path, rate, depth=24):
    fmt = ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if depth == 24 else []
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=duration=1:sample_rate={rate}",
            "-metadata",
            "title=Tone",
            "-c:a",
            "flac",
            *fmt,
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_flac_above_caps_is_reencoded_in_place(tmp_path):
    from mutagen.flac import FLAC

    src = tmp_path / "hi.flac"
    _sine(src, 96_000)
    out = asyncio.run(
        transcode.transcode_file_async(
            src, fmt="flac", max_sample_rate=48_000, max_bit_depth=16
        )
    )
    assert out == src
    info = FLAC(str(out)).info
    assert (info.sample_rate, info.bits_per_sample) == (48_000, 16)
    assert FLAC(str(out))["title"] == ["Tone"]

    # Within the caps now: left alone.
    before = out.stat().st_mtime_ns
    asyncio.run(
        transcode.transcode_file_async(
            out, fmt="flac", max_sample_rate=48_000, max_bit_depth=16
        )
    )
    assert out.stat().st_mtime_ns == before


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize(
    ("fmt", "suffix", "codec"), [("aac", ".m4a", "aac"), ("opus", ".opus", "opus")]
)
def test_lossy_targets(tmp_path, fmt, suffix, codec):
    src = tmp_path / "in.flac"
    _sine(src, 96_000)
    out = asyncio.run(transcode.transcode_file_async(src, fmt=fmt))
    assert out.suffix == suffix and not src.exists()
    probed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert probed == f"{codec},48000"
    # An AAC .m4a is not mistaken for ALAC, nor the reverse.
    assert transcode.already_in_target_format(out, fmt)
    if fmt == "aac":
        assert not transcode.already_in_target_format(out, "alac")
