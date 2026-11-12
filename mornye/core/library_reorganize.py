"""Reorganise an existing library into a folder template.

Downloads are placed by the download template; files that are already on disk
(or were downloaded under an older one) stay wherever they were. This plans
and applies the moves that bring a folder in line with a template such as
``{album_artist}/{album}/{track} - {title}``, using each file's own tags.
Nothing is overwritten and nothing leaves the library root.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .models import sanitize
from .tagger import SUPPORTED_SUFFIXES, read_embedded_tags

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "{album_artist}/{album}/{track} - {title}"
PLACEHOLDERS = (
    "artist",
    "album_artist",
    "album",
    "title",
    "track",
    "disc",
    "year",
    "genre",
)
_FIELD_RE = re.compile(r"\{(\w+)\}")
#: Files that travel with a track and share its stem.
_SIDECARS = (".lrc", ".txt")


def validate_template(template: str) -> str | None:
    """None when `template` is usable, else a message saying why not."""
    if not template or not template.strip():
        return "The template is empty"
    unknown = sorted(set(_FIELD_RE.findall(template)) - set(PLACEHOLDERS))
    if unknown:
        return (
            f"Unknown placeholder(s): {', '.join('{' + u + '}' for u in unknown)}. "
            f"Use: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"
        )
    if ".." in template.replace("\\", "/").split("/") or template.startswith(
        ("/", "\\")
    ):
        return "The template must be a relative path"
    if "{title}" not in template:
        return "The template must contain {title}, or tracks would collide"
    return None


def _num(value: str, width: int) -> str:
    head = (value or "").split("/")[0].strip()
    return head.zfill(width) if head.isdigit() else ""


def _render(template: str, tags: dict[str, str], fallback_title: str) -> str:
    artist = tags.get("ARTIST", "")
    values = {
        "artist": artist,
        "album_artist": tags.get("ALBUMARTIST") or artist,
        "album": tags.get("ALBUM", ""),
        "title": tags.get("TITLE") or fallback_title,
        "track": _num(tags.get("TRACKNUMBER", ""), 2),
        "disc": _num(tags.get("DISCNUMBER", ""), 1),
        "year": (tags.get("DATE") or tags.get("YEAR") or "")[:4],
        "genre": tags.get("GENRE", ""),
    }
    parts = []
    for segment in template.replace("\\", "/").split("/"):
        rendered = _FIELD_RE.sub(lambda m: values.get(m.group(1), ""), segment)
        # "01 - Title" with no track number would read " - Title".
        rendered = re.sub(r"^\s*[-–.]\s*", "", rendered)
        parts.append(sanitize(rendered, "Unknown"))
    return "/".join(parts)


def plan_reorganize(root: str | Path, template: str) -> list[dict[str, str]]:
    """The moves needed to bring `root` in line with `template`.

    Each entry: ``{src, dest, status}`` with status "move", "same" (already
    in place) or "conflict" (the destination exists). Paths are absolute.
    """
    root_p = Path(root).resolve()
    error = validate_template(template)
    if error:
        raise ValueError(error)
    if not root_p.is_dir():
        raise ValueError(f"Not a folder: {root_p}")

    plan: list[dict[str, str]] = []
    claimed: set[Path] = set()
    for src in sorted(root_p.rglob("*")):
        if not src.is_file() or src.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if any(part.startswith(".") for part in src.relative_to(root_p).parts):
            continue
        tags = read_embedded_tags(src, include_cover=False).tags
        rel = _render(template, tags, src.stem)
        dest = root_p / (rel + src.suffix.lower())
        if src == dest:
            status = "same"
        elif dest in claimed or dest.exists():
            status = "conflict"
        else:
            status = "move"
            claimed.add(dest)
        plan.append({"src": str(src), "dest": str(dest), "status": status})
    return plan


def apply_reorganize(root: str | Path, moves: list[dict[str, str]]) -> list[dict]:
    """Performs the "move" entries of a plan. Re-checks everything: a move
    that would leave `root`, or land on an existing file, is refused."""
    root_p = Path(root).resolve()
    results: list[dict] = []
    touched_dirs: set[Path] = set()
    for move in moves:
        src, dest = Path(move["src"]).resolve(), Path(move["dest"]).resolve()
        res = {"src": str(src), "dest": str(dest), "success": False}
        try:
            if not (src.is_relative_to(root_p) and dest.is_relative_to(root_p)):
                raise ValueError("Outside the library folder")
            if not src.is_file():
                raise ValueError("Source no longer exists")
            if dest.exists():
                raise ValueError("A file already exists at the destination")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            for ext in _SIDECARS:
                side = src.with_suffix(ext)
                target = dest.with_suffix(ext)
                if side.is_file() and not target.exists():
                    shutil.move(str(side), str(target))
            touched_dirs.add(src.parent)
            res["success"] = True
        except Exception as exc:
            res["error"] = str(exc)
        results.append(res)

    # Tidy up folders the moves emptied, never the root itself.
    for directory in sorted(touched_dirs, key=lambda d: len(d.parts), reverse=True):
        d = directory
        while d != root_p and d.is_relative_to(root_p):
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    return results
