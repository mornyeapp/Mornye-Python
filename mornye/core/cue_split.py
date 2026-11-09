"""Split a single-file album plus its `.cue` sheet into one file per track.

Rips and archival releases often ship as one long FLAC (or WAV/APE) with a
CUE sheet giving each track's start. Nothing else in the library tools —
dedup, upgrade, retag — can see individual songs in that shape. The mobile
app splits them (cue_split_library_service); this is the same for Python.

Splitting re-encodes each segment with FFmpeg rather than stream-copying:
a stream copy can only cut on a frame/packet boundary, which is not where
CUE indexes (1/75 s) put track starts, and a FLAC → FLAC re-encode is
lossless anyway. The source files are never modified or removed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .models import fit_name_bytes

logger = logging.getLogger(__name__)

FRAMES_PER_SECOND = 75

#: Output format → (extension, ffmpeg codec arguments).
OUTPUT_FORMATS: dict[str, tuple[str, list[str]]] = {
    "flac": (".flac", ["-c:a", "flac", "-compression_level", "8"]),
    "alac": (".m4a", ["-c:a", "alac"]),
    "wav": (".wav", ["-c:a", "pcm_s24le"]),
}

#: Tried in order when no encoding is given. UTF-8 first (with or without a
#: BOM); cp1252 last because it decodes almost any byte string, so it can
#: only ever be the fallback.
_ENCODINGS = ("utf-8-sig", "shift_jis", "gb18030", "cp1252")


class CueError(Exception):
    """A sheet that cannot be split as asked."""


@dataclass
class CueTrack:
    number: int
    title: str = ""
    performer: str = ""
    isrc: str = ""
    start_s: float | None = None  # INDEX 01, seconds into its FILE
    file: str = ""


@dataclass
class CueSheet:
    path: Path | None = None
    title: str = ""
    performer: str = ""
    genre: str = ""
    date: str = ""
    tracks: list[CueTrack] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(t.file for t in self.tracks))


@dataclass
class SplitPlan:
    track: CueTrack
    source: Path
    start_s: float
    end_s: float | None  # None: to the end of the source
    output: Path


def parse_timestamp(value: str) -> float:
    """`MM:SS:FF` (75 frames per second) → seconds."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})\s*", value or "")
    if not m:
        raise CueError(f"bad CUE timestamp: {value!r}")
    minutes, seconds, frames = (int(x) for x in m.groups())
    if seconds >= 60 or frames >= FRAMES_PER_SECOND:
        raise CueError(f"bad CUE timestamp: {value!r}")
    return minutes * 60 + seconds + frames / FRAMES_PER_SECOND


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def decode_cue_bytes(raw: bytes, encoding: str | None = None) -> str:
    if encoding:
        return raw.decode(encoding)
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_cue(text: str, path: Path | None = None) -> CueSheet:
    sheet = CueSheet(path=path)
    current_file = ""
    track: CueTrack | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        rest = rest.strip()
        if keyword == "FILE":
            # FILE "name with spaces.flac" WAVE — the type is the last word.
            m = re.match(r'^(".*"|\S+)(?:\s+\S+)?$', rest)
            current_file = _unquote(m.group(1) if m else rest)
        elif keyword == "TRACK":
            parts = rest.split()
            if not parts or not parts[0].isdigit():
                raise CueError(f"bad TRACK line: {line!r}")
            if len(parts) > 1 and parts[1].upper() != "AUDIO":
                track = None  # a data track on a mixed-mode disc
                continue
            track = CueTrack(number=int(parts[0]), file=current_file)
            sheet.tracks.append(track)
        elif keyword == "TITLE":
            if track is None:
                sheet.title = _unquote(rest)
            else:
                track.title = _unquote(rest)
        elif keyword == "PERFORMER":
            if track is None:
                sheet.performer = _unquote(rest)
            else:
                track.performer = _unquote(rest)
        elif keyword == "ISRC" and track is not None:
            track.isrc = _unquote(rest)
        elif keyword == "INDEX" and track is not None:
            parts = rest.split()
            if len(parts) == 2 and parts[0] == "01":
                track.start_s = parse_timestamp(parts[1])
        elif keyword == "REM":
            sub, _, value = rest.partition(" ")
            sub = sub.upper()
            if sub == "GENRE":
                sheet.genre = _unquote(value)
            elif sub == "DATE":
                sheet.date = _unquote(value)
    missing = [t.number for t in sheet.tracks if t.start_s is None]
    if missing:
        raise CueError(f"tracks without INDEX 01: {missing}")
    if not sheet.tracks:
        raise CueError("no audio tracks in the sheet")
    return sheet


def read_cue(path: str | Path, encoding: str | None = None) -> CueSheet:
    path = Path(path)
    return parse_cue(decode_cue_bytes(path.read_bytes(), encoding), path)


_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(value: str) -> str:
    cleaned = _UNSAFE_CHARS.sub("_", value).strip().rstrip(".")
    # Bytes, not characters: 150 CJK characters are 450 bytes, past the
    # 255-byte limit of Linux filesystems.
    return fit_name_bytes(cleaned, 180) or "Track"


def _resolve_source(cue_path: Path, name: str) -> Path:
    """The audio file a FILE line names, beside the sheet.

    Sheets often name the file the rip was made from (`album.wav`) while the
    folder holds the compressed copy (`album.flac`), so a missing name falls
    back to the same stem with another extension. Never leaves the sheet's
    directory.
    """
    base = cue_path.parent
    candidate = (base / Path(name.replace("\\", "/")).name).resolve()
    if candidate.parent != base.resolve():
        raise CueError(f"FILE outside the sheet's folder: {name!r}")
    if candidate.is_file():
        return candidate
    stems = [Path(name.replace("\\", "/")).stem, cue_path.stem]
    for stem in dict.fromkeys(stems):
        for ext in (".flac", ".wav", ".ape", ".wv", ".m4a", ".aiff", ".tta"):
            alt = base / f"{stem}{ext}"
            if alt.is_file():
                return alt
    raise CueError(f"audio file not found for FILE {name!r}")


def plan_split(
    sheet: CueSheet,
    output_dir: str | Path | None = None,
    fmt: str = "flac",
) -> list[SplitPlan]:
    if sheet.path is None:
        raise CueError("sheet has no path to resolve its audio against")
    if fmt not in OUTPUT_FORMATS:
        raise CueError(
            f"unknown format {fmt!r}; expected one of {sorted(OUTPUT_FORMATS)}"
        )
    ext = OUTPUT_FORMATS[fmt][0]
    out = Path(output_dir) if output_dir else sheet.path.parent
    sources = {name: _resolve_source(sheet.path, name) for name in sheet.files}
    width = max(2, len(str(max(t.number for t in sheet.tracks))))

    plans: list[SplitPlan] = []
    for i, track in enumerate(sheet.tracks):
        nxt = sheet.tracks[i + 1] if i + 1 < len(sheet.tracks) else None
        end = nxt.start_s if nxt is not None and nxt.file == track.file else None
        start = track.start_s or 0.0
        if end is not None and end <= start:
            raise CueError(f"track {track.number} ends before it starts")
        title = _safe_filename(track.title or f"Track {track.number}")
        plans.append(
            SplitPlan(
                track=track,
                source=sources[track.file],
                start_s=start,
                end_s=end,
                output=out / f"{track.number:0{width}d} - {title}{ext}",
            )
        )
    for plan in plans:
        if plan.output.resolve() == plan.source.resolve():
            raise CueError(f"output would overwrite the source: {plan.output}")
    return plans


def ffmpeg_command(
    plan: SplitPlan, sheet: CueSheet, fmt: str, total: int, ffmpeg: str = "ffmpeg"
) -> list[str]:
    track = plan.track
    tags = {
        "title": track.title,
        "artist": track.performer or sheet.performer,
        "album_artist": sheet.performer,
        "album": sheet.title,
        "track": f"{track.number}/{total}",
        "genre": sheet.genre,
        "date": sheet.date,
        "isrc": track.isrc,
    }
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    cmd += ["-ss", f"{plan.start_s:.6f}"]
    if plan.end_s is not None:
        cmd += ["-to", f"{plan.end_s:.6f}"]
    cmd += ["-i", str(plan.source), "-map", "0:a:0", "-map_metadata", "-1"]
    cmd += OUTPUT_FORMATS[fmt][1]
    for key, value in tags.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(plan.output))
    return cmd


@dataclass
class SplitResult:
    cue: Path
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    error: str = ""


async def split_cue_async(
    cue_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    fmt: str = "flac",
    encoding: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> SplitResult:
    cue_path = Path(cue_path)
    result = SplitResult(cue=cue_path)
    try:
        sheet = read_cue(cue_path, encoding)
        plans = plan_split(sheet, output_dir, fmt)
    except (CueError, OSError, UnicodeDecodeError, LookupError) as exc:
        result.error = str(exc)
        return result

    if dry_run:
        result.written = [p.output for p in plans]
        return result

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        result.error = "ffmpeg not found on PATH"
        return result

    for plan in plans:
        if plan.output.exists() and not overwrite:
            result.skipped.append(plan.output)
            continue
        plan.output.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed into place, so an
        # interrupted split never leaves a truncated file under the real name.
        tmp = plan.output.with_name(f".{plan.output.stem}.part{plan.output.suffix}")
        cmd = ffmpeg_command(plan, sheet, fmt, len(plans), ffmpeg)
        cmd[-1] = str(tmp)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            result.error = (
                f"ffmpeg failed on track {plan.track.number}: "
                f"{stderr.decode(errors='replace').strip()[:500]}"
            )
            return result
        tmp.replace(plan.output)
        result.written.append(plan.output)
    return result


def find_cue_files(root: str | Path, recursive: bool = True) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in root.glob(pattern) if p.suffix.lower() == ".cue" and p.is_file()
    )
