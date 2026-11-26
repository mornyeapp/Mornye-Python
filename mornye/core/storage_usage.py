"""core/storage_usage.py — what Mornye keeps on disk and in memory, for
Settings › Storage.

`cache_admin` covers the CLI's view of `~/.mornye/.cache`; this is the
wider picture a user asks about ("why is Mornye using 300 MB?"): the
database, the cover thumbnails, the Fix Local Files results kept in the
database, the decoded playback copies, the scan indexes, and the memory the
app and the helpers it starts (Node for extensions, mpv, a solver browser)
are holding right now.

Every entry says whether clearing it is safe. Only regenerable state is
ever clearable: what goes costs a re-fetch or a re-scan, never something the
user typed. Extensions, settings and history are reported and left alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import cache_admin
from .paths import cache_path, data_path

logger = logging.getLogger(__name__)

#: Entries clear() accepts. "all" clears every one of them.
CLEARABLE = ("local_scan", "thumbs", "responses", "library_index", "playback", "small")


def _dir_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total = count = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
                count += 1
            except OSError:
                continue
    return total, count


def _db_files() -> list[Path]:
    from .db import db_path

    base = db_path()
    return [
        base,
        base.with_name(base.name + "-wal"),
        base.with_name(base.name + "-shm"),
    ]


def _scan_cache_size() -> tuple[int, int]:
    """Bytes and row count of the Fix Local Files results in the database."""
    try:
        from . import db

        row = (
            db.connection()
            .execute(
                "SELECT COALESCE(SUM(LENGTH(entry) + LENGTH(path)), 0), COUNT(*)"
                " FROM local_scan_cache"
            )
            .fetchone()
        )
        return int(row[0] or 0), int(row[1] or 0)
    except Exception:
        logger.debug("[storage] could not size local_scan_cache", exc_info=True)
        return 0, 0


def _webview_cache_dirs() -> list[Path]:
    dirs = [cache_path("webview")]
    if sys.platform == "darwin":
        dirs.append(Path.home() / "Library" / "Caches" / "app.mornye.desktop")
    return [d for d in dirs if d.exists()]


def _entry(
    item_id: str, label: str, desc: str, paths: list[Path], clearable: bool
) -> dict:
    size = files = 0
    for p in paths:
        s, n = _dir_size(p)
        size += s
        files += n
    return {
        "id": item_id,
        "label": label,
        "description": desc,
        "bytes": size,
        "files": files,
        "clearable": clearable,
    }


def _disk_items() -> list[dict]:
    root = cache_admin.cache_root()
    db_entry = _entry(
        "database",
        "Database",
        "Library, playlists, history, stats and settings. Compact rewrites it "
        "without the space deleted rows leave behind.",
        _db_files(),
        False,
    )
    db_entry["files"] = 1
    db_entry["compactable"] = True
    scan_bytes, scan_rows = _scan_cache_size()
    return [
        db_entry,
        {
            "id": "local_scan",
            "label": "Fix Local Files results",
            "description": "Each scanned file's lookup and cover preview, so a "
            "rescan is instant. Kept inside the database, which is compacted "
            "after clearing.",
            "bytes": scan_bytes,
            "files": scan_rows,
            "clearable": True,
        },
        _entry(
            "thumbs",
            "Cover thumbnails",
            "Covers shrunk for the Player and the library; made again as they are shown.",
            [data_path("thumbs")],
            True,
        ),
        _entry(
            "responses",
            "Web responses",
            "Cached answers from Spotify, MusicBrainz, lyrics and enrichment services.",
            [root / cache_admin.RESPONSES_DIR],
            True,
        ),
        _entry(
            "library_index",
            "Library indexes",
            "What the library and duplicate scans read from each file; the next "
            "scan reads the files again.",
            [cache_path("library-index"), cache_path("library-dedup")],
            True,
        ),
        _entry(
            "playback",
            "Playback copies",
            "Files decoded for the Player when it cannot play the original directly.",
            [cache_path("playback")],
            True,
        ),
        _entry(
            "small",
            "Lookups and logs",
            "ISRC matches, recent fetches, provider order and the error log.",
            [root / n for n in cache_admin.PRUNABLE_FILES] + [root / "errors.log"],
            True,
        ),
        _entry(
            "extensions",
            "Extensions",
            "Installed extensions and their own data; managed in the Extensions tab.",
            [data_path("extensions"), data_path("extension_storage")],
            False,
        ),
        _entry(
            "webview",
            "Window cache",
            "The app window's own web cache, in use while Mornye runs.",
            _webview_cache_dirs(),
            False,
        ),
    ]


def _friendly(comm: str) -> str:
    base = os.path.basename(comm).lower()
    if "node" in base:
        return "Extensions (Node.js)"
    if "mpv" in base:
        return "Player (mpv)"
    if "ffmpeg" in base or "ffprobe" in base:
        return "ffmpeg"
    if "chrom" in base:
        return "Verification browser"
    if "webkit" in base or "webcontent" in base:
        return "Window"
    if "python" in base:
        return "Mornye workers"
    return os.path.basename(comm) or "Other"


def _windows_working_set() -> int:
    """This process's working set on Windows (GetProcessMemoryInfo), 0 when
    it cannot be read. Windows has neither `ps` nor `resource`."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        windll = ctypes.WinDLL  # type: ignore[attr-defined]
        kernel32 = windll("kernel32", use_last_error=True)
        psapi = windll("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
        return int(counters.WorkingSetSize) if ok else 0
    except Exception:
        return 0


def _process_memory() -> dict:
    """Resident memory of this process and every process it started.

    `ps` rather than psutil (not a dependency): one call lists the whole
    table and the tree is walked down from our pid. Without `ps` only this
    process is measured: its working set on Windows, its peak elsewhere.
    """
    me = os.getpid()
    out = ""
    if os.name == "posix":
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,rss=,comm="],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except Exception:
            out = ""
    rows: dict[int, tuple[int, int, str]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rows[int(parts[0])] = (int(parts[1]), int(parts[2]) * 1024, parts[3])
        except ValueError:
            continue
    if me not in rows:
        peak = _windows_working_set() if os.name == "nt" else 0
        if not peak:
            try:
                import resource

                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if sys.platform != "darwin":
                    peak *= 1024  # KiB on Linux, bytes on macOS
            except Exception:
                pass
        return {
            "total_bytes": peak,
            "processes": [{"name": "Mornye", "bytes": peak, "count": 1}],
        }

    children: dict[int, list[int]] = {}
    for pid, (ppid, _rss, _comm) in rows.items():
        children.setdefault(ppid, []).append(pid)
    tree, stack = [], [me]
    while stack:
        pid = stack.pop()
        tree.append(pid)
        stack.extend(children.get(pid, []))

    groups: dict[str, dict] = {}
    for pid in tree:
        _ppid, rss, comm = rows[pid]
        if pid != me and os.path.basename(comm).strip("()") == "ps":
            continue  # this very measurement
        name = "Mornye" if pid == me else _friendly(comm)
        group = groups.setdefault(name, {"name": name, "bytes": 0, "count": 0})
        group["bytes"] += rss
        group["count"] += 1
    procs = sorted(groups.values(), key=lambda g: -g["bytes"])
    return {"total_bytes": sum(g["bytes"] for g in procs), "processes": procs}


def report() -> dict:
    items = _disk_items()
    return {
        "data_dir": str(data_path()),
        "disk": items,
        "disk_total_bytes": sum(i["bytes"] for i in items),
        "clearable_bytes": sum(i["bytes"] for i in items if i["clearable"]),
        "memory": _process_memory(),
    }


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def clear(item_id: str) -> dict:
    """Empties one clearable entry, or every one of them for "all"."""
    ids = list(CLEARABLE) if item_id == "all" else [item_id]
    unknown = [i for i in ids if i not in CLEARABLE]
    if unknown:
        msg = f"{unknown[0]!r} cannot be cleared"
        raise ValueError(msg)
    before = {i["id"]: i["bytes"] for i in _disk_items()}
    root = cache_admin.cache_root()
    for one in ids:
        if one == "local_scan":
            from . import db

            with db.transaction() as conn:
                conn.execute("DELETE FROM local_scan_cache")
        elif one == "thumbs":
            _remove(data_path("thumbs"))
        elif one == "responses":
            _remove(root / cache_admin.RESPONSES_DIR)
        elif one == "library_index":
            _remove(cache_path("library-index"))
            _remove(cache_path("library-dedup"))
        elif one == "playback":
            _remove(cache_path("playback"))
        elif one == "small":
            for name in (*cache_admin.PRUNABLE_FILES, "errors.log"):
                _remove(root / name)
    result: dict = {"cleared": ids, "freed_bytes": sum(before.get(i, 0) for i in ids)}
    if "local_scan" in ids:
        # The rows are gone, but the file keeps its size until rewritten.
        try:
            result["compact"] = compact()
        except Exception as e:  # a busy database: the rows are still gone
            result["compact_error"] = str(e)
    return result


def compact() -> dict:
    """VACUUM the database: rewrite it without its free pages."""
    from . import db

    files = _db_files()
    before = sum(_dir_size(p)[0] for p in files)
    conn = db.connection()
    if conn.in_transaction:  # VACUUM cannot run inside one
        conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = sum(_dir_size(p)[0] for p in files)
    return {
        "before_bytes": before,
        "after_bytes": after,
        "freed_bytes": max(0, before - after),
    }
