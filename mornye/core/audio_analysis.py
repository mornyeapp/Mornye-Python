"""Technical facts about an audio file, and its spectrogram.

The spectrogram is the quickest honest answer to "is this really lossless /
Hi-Res?": a transcoded MP3 shows a hard shelf at 16 kHz, an upsampled CD rip
shows nothing above 22 kHz. It is drawn by ffmpeg's `showspectrumpic`.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from .errors import ErrorKind, MornyeError
from .transcode import _run_ffmpeg


def probe_audio(path: str | Path) -> dict[str, object]:
    """Codec, sample rate, bit depth, bitrate, channels, duration and size."""
    p = Path(path)
    if not p.is_file():
        raise MornyeError(ErrorKind.FILE_IO, f"File not found: {p}")
    info: dict[str, object] = {
        "file": p.name,
        "size_bytes": p.stat().st_size,
        "format": p.suffix.lstrip(".").upper(),
    }
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(p))
        meta = getattr(audio, "info", None)
        if meta is not None:
            info["codec"] = type(audio).__name__
            info["sample_rate"] = int(getattr(meta, "sample_rate", 0) or 0)
            info["bit_depth"] = int(getattr(meta, "bits_per_sample", 0) or 0)
            info["channels"] = int(getattr(meta, "channels", 0) or 0)
            info["bitrate"] = int(getattr(meta, "bitrate", 0) or 0)
            info["duration_s"] = round(float(getattr(meta, "length", 0) or 0), 1)
    except Exception as exc:  # unreadable stream: still return what we have
        info["error"] = str(exc)
    if not info.get("sample_rate") or (not info.get("bitrate") and not info.get("bit_depth")):
        _fill_from_ffprobe(p, info)
    return info


def _fill_from_ffprobe(path: Path, info: dict[str, object]) -> None:
    """mutagen reports no sample rate or bitrate for some containers (Opus,
    for one); ffprobe always knows. Only fills what is still missing."""
    import json
    import subprocess

    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries",
                "stream=sample_rate,bit_rate,channels,bits_per_raw_sample:format=bit_rate,duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
        data = json.loads(out or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    def num(value: object) -> int:
        try:
            return int(float(str(value)))
        except ValueError:
            return 0

    if not info.get("sample_rate"):
        info["sample_rate"] = num(stream.get("sample_rate"))
    if not info.get("channels"):
        info["channels"] = num(stream.get("channels"))
    if not info.get("bit_depth"):
        info["bit_depth"] = num(stream.get("bits_per_raw_sample"))
    if not info.get("bitrate"):
        info["bitrate"] = num(stream.get("bit_rate")) or num(fmt.get("bit_rate"))
    if not info.get("duration_s"):
        info["duration_s"] = round(float(fmt.get("duration") or 0), 1)


async def spectrogram_png_async(
    path: str | Path, *, width: int = 1000, height: int = 512
) -> bytes:
    """A PNG spectrogram of the whole file, with frequency/time legends."""
    p = Path(path)
    if not await asyncio.to_thread(p.is_file):
        raise MornyeError(ErrorKind.FILE_IO, f"File not found: {p}")
    with tempfile.TemporaryDirectory(prefix="mornye-spec-") as tmp:
        out = Path(tmp) / "spec.png"
        rc, stderr = await _run_ffmpeg(
            "-y",
            "-v",
            "error",
            "-i",
            str(p),
            "-lavfi",
            f"showspectrumpic=s={width}x{height}:legend=1:color=intensity",
            "-frames:v",
            "1",
            str(out),
        )
        if rc != 0 or not out.is_file():
            raise MornyeError(
                ErrorKind.FILE_IO, f"ffmpeg could not draw a spectrogram: {stderr}"
            )
        return await asyncio.to_thread(out.read_bytes)
