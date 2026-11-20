"""Decrypting extension downloads (provider._decrypt_mp4_async), and
keeping Dolby audio out of the transcode step."""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from mornye.extensions.provider import (
    _decrypt_mp4_async,
    _decryption_key_candidates,
)

KEY = "00112233445566778899aabbccddeeff"
KID = "0123456789abcdef0123456789abcdef"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _encrypted(out: Path, codec: str, muxer: str) -> Path:
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:a",
        codec,
        "-encryption_scheme",
        "cenc-aes-ctr",
        "-encryption_key",
        KEY,
        "-encryption_kid",
        KID,
        "-f",
        muxer,
        str(out),
    )
    return out


def _decoded_md5(path: Path, key: str | None = None) -> str:
    cmd = ["ffmpeg", "-v", "error"]
    if key:
        cmd += ["-decryption_key", key]
    cmd += ["-i", str(path), "-f", "md5", "-"]
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_key_candidates_follow_the_mornye_app() -> None:
    assert _decryption_key_candidates(f"0x{KEY}")[:2] == [f"0x{KEY}", KEY]
    b64 = base64.b64encode(bytes.fromhex(KEY)).decode()
    assert KEY in _decryption_key_candidates(b64)
    assert _decryption_key_candidates("  ") == []


@needs_ffmpeg
def test_aac_is_decrypted_into_mp4(tmp_path) -> None:
    from mutagen import File

    enc = _encrypted(tmp_path / "t.m4a", "aac", "mp4")
    expected = _decoded_md5(enc, KEY)
    assert File(str(enc)).info.codec == "enca"

    assert asyncio.run(_decrypt_mp4_async(str(enc), KEY))

    assert File(str(enc)).info.codec.startswith("mp4a")
    assert _decoded_md5(enc) == expected
    assert not list(tmp_path.glob("*.decrypting*"))


@needs_ffmpeg
def test_a_codec_mp4_refuses_is_decrypted_into_mov_under_the_same_name(
    tmp_path,
) -> None:
    """Amazon's AC-4 case. IMA ADPCM is refused by the MP4 muxer with the same
    "Could not find tag for codec" error, and accepted by MOV."""
    enc = _encrypted(tmp_path / "t.mp4", "adpcm_ima_qt", "mov")
    expected = _decoded_md5(enc, KEY)

    assert asyncio.run(_decrypt_mp4_async(str(enc), KEY))

    assert enc.name == "t.mp4"
    assert _decoded_md5(enc) == expected


@needs_ffmpeg
def test_a_failed_decryption_leaves_the_download_untouched(tmp_path) -> None:
    out = tmp_path / "t.mp4"
    out.write_bytes(b"not an mp4 at all" * 100)
    before = out.read_bytes()

    assert not asyncio.run(_decrypt_mp4_async(str(out), KEY))
    assert out.read_bytes() == before
    assert not list(tmp_path.glob("*.decrypting*"))


@needs_ffmpeg
def test_dolby_audio_is_not_transcoded(tmp_path) -> None:
    """Converting Atmos to ALAC would drop the objects (and AC-4 cannot even
    be decoded): the download is kept as it arrived."""
    from mornye.core.transcode import transcode_file_async

    src = tmp_path / "atmos.m4a"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1",
        "-af",
        "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0",
        "-c:a",
        "eac3",
        "-f",
        "mp4",
        str(src),
    )
    before = src.read_bytes()

    assert asyncio.run(transcode_file_async(src, fmt="alac")) == src
    assert src.read_bytes() == before


def _box(kind: bytes, payload: bytes) -> bytes:
    import struct

    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _protected_original(config: bytes) -> bytes:
    """A minimal encrypted-style file: one sound track whose `enca` entry
    holds a codec configuration box and its `sinf`."""
    entry = _box(
        b"enca",
        bytes(6)
        + b"\0\1"
        + bytes(8)
        + b"\0\2\0\x10"
        + bytes(4)
        + b"\xbb\x80\0\0"
        + config
        + _box(b"sinf", _box(b"frma", b"ac-4")),
    )
    stsd = _box(b"stsd", bytes(4) + b"\0\0\0\1" + entry)
    hdlr = _box(b"hdlr", bytes(8) + b"soun" + bytes(12) + b"\0")
    mdia = _box(b"mdia", hdlr + _box(b"minf", _box(b"stbl", stsd)))
    return _box(b"ftyp", b"mp42") + _box(b"moov", _box(b"trak", mdia))


@needs_ffmpeg
@pytest.mark.parametrize("faststart", [False, True])
def test_a_dropped_codec_config_is_copied_back(tmp_path, faststart) -> None:
    """ffmpeg's MOV muxer writes AC-4 without its `dac4`. Copied back from
    the encrypted original, the file must still decode to the same audio,
    with the moov after the media (ffmpeg's default) or before it."""
    from mornye.core.mp4_config import codec_config_boxes, restore_codec_config

    target = tmp_path / "t.m4a"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:a",
        "aac",
        *(["-movflags", "+faststart"] if faststart else []),
        str(target),
    )
    before = _decoded_md5(target)
    dac4 = _box(b"dac4", bytes.fromhex("20a6018040000000"))

    added = restore_codec_config(_protected_original(dac4), target)

    assert added == ["dac4"]
    assert codec_config_boxes(target.read_bytes())[b"dac4"] == dac4
    assert _decoded_md5(target) == before
    # Nothing missing any more: a second pass changes nothing.
    assert restore_codec_config(_protected_original(dac4), target) == []
