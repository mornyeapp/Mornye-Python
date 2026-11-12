"""What the in-app player actually plays.

The rule is *never lower the quality*. A file the browser engine can decode
(FLAC, MP3, WAV, Ogg, Opus, AAC) is served byte for byte, untouched. A file it
cannot (ALAC, AIFF, WavPack, TTA, APE, WMA…) is decoded once and re-encoded as
**FLAC**, which is lossless: same sample rate, same bit depth, same samples. The
copy is cached, so the second play is instant. Nothing is ever turned into MP3.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from .errors import ErrorKind, MornyeError
from .paths import cache_path
from .transcode import (
    _encode_command,
    _probe_sample_rate,
    _probe_source,
    _run_ffmpeg,
    ensure_ffmpeg_available,
    format_spec,
)

logger = logging.getLogger(__name__)

#: Decoded natively by every engine the app runs in (WKWebView, WebView2,
#: WebKitGTK, Chromium, Firefox).
DIRECT_SUFFIXES = frozenset({".flac", ".mp3", ".wav", ".wave", ".ogg", ".oga", ".opus", ".aac"})
#: MPEG-4 audio: plain AAC plays everywhere, ALAC only in some engines.
MP4_SUFFIXES = frozenset({".m4a", ".mp4", ".m4b"})

_CACHE_KEEP = 24  # decoded copies kept on disk


def _mp4_is_alac(path: Path) -> bool:
    try:
        from mutagen.mp4 import MP4

        return str(MP4(str(path)).info.codec).lower().startswith("alac")
    except Exception:
        return False


def needs_lossless_copy(path: Path) -> bool:
    """True when the engine cannot play `path` as it is."""
    suffix = path.suffix.lower()
    if suffix in DIRECT_SUFFIXES:
        return False
    if suffix in MP4_SUFFIXES:
        return _mp4_is_alac(path)
    return True


def _cache_dir() -> Path:
    directory = cache_path("playback")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _copy_path(src: Path) -> Path:
    stat = src.stat()
    key = f"{src}|{stat.st_mtime_ns}|{stat.st_size}".encode()
    return _cache_dir() / f"{hashlib.sha1(key).hexdigest()[:24]}.flac"  # noqa: S324


def _trim_cache() -> None:
    copies = sorted(_cache_dir().glob("*.flac"), key=lambda p: p.stat().st_mtime)
    for old in copies[: max(0, len(copies) - _CACHE_KEEP)]:
        try:
            old.unlink()
        except OSError:
            logger.debug("[playback] could not remove %s", old)


async def playable_file_async(path: str | Path) -> tuple[Path, str]:
    """(file to stream, "original" | "flac-copy") for `path`."""
    src = Path(path)
    if not await asyncio.to_thread(src.is_file):
        raise MornyeError(ErrorKind.FILE_IO, f"File not found: {src}")
    if not await asyncio.to_thread(needs_lossless_copy, src):
        return src, "original"

    dest = await asyncio.to_thread(_copy_path, src)
    if await asyncio.to_thread(lambda: dest.is_file() and dest.stat().st_size > 0):
        dest.touch()
        return dest, "flac-copy"

    await asyncio.to_thread(ensure_ffmpeg_available, "flac")
    bit_depth, _lossless = await asyncio.to_thread(_probe_source, src)
    sample_rate = await asyncio.to_thread(_probe_sample_rate, src)
    tmp = dest.with_name(f".{dest.stem}.part.flac")
    # The same encoder settings as a lossless FLAC conversion (rate and depth
    # carried over untouched), with the cheapest compression: this copy is for
    # listening, so decode speed matters and size does not.
    cmd = _encode_command(
        format_spec("flac"), src, tmp, "", bit_depth, sample_rate=sample_rate
    )
    cmd[cmd.index("-compression_level") + 1] = "1"
    rc, stderr = await _run_ffmpeg(*cmd)
    if rc != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MornyeError(
            ErrorKind.FILE_IO, f"ffmpeg could not decode {src.name} for playback: {stderr}"
        )
    tmp.replace(dest)
    await asyncio.to_thread(_trim_cache)
    return dest, "flac-copy"


def quality_label(info: dict[str, object], mode: str, suffix: str) -> str:
    """What the listener is hearing, e.g. ``FLAC 24-bit / 96 kHz``, with a note
    when the stream is a lossless copy of a format the engine cannot play."""
    rate = int(info.get("sample_rate") or 0)
    depth = int(info.get("bit_depth") or 0)
    bitrate = int(info.get("bitrate") or 0)
    fmt = suffix.lstrip(".").upper()
    parts = [fmt]
    if depth and rate:
        khz = rate / 1000
        parts.append(f"{depth}-bit / {khz:g} kHz")
    elif rate:
        parts.append(f"{rate / 1000:g} kHz")
    if bitrate and not depth:
        parts.append(f"{round(bitrate / 1000)} kbps")
    label = " ".join(parts)
    if mode == "flac-copy":
        label += " · lossless FLAC copy"
    return label
