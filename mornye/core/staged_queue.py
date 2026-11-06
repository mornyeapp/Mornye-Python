"""staged_queue.py — things to download later, kept until you press Start.

The live download queue (the bottom bar, the Downloading list) only exists
while a batch runs, and a batch starts the moment Download is pressed. This
is the other kind: "Add to queue" on a track, album, playlist or discography
puts it here, with its tracks written down in full — the tracklist on screen
is replaced by the next fetch and gone after a restart — and nothing happens
until Start (one item) or Start all.

Stored as one small JSON file under the cache directory; the list is short
and read whole by the page, so a table would buy nothing.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import cache_path

KINDS = ("track", "album", "playlist", "artist")
STATUSES = ("pending", "running", "done", "failed")
#: Items kept. Generous: this is a to-do list, not a log.
MAX_ITEMS = 500

_lock = threading.RLock()


def _file() -> Path:
    return cache_path("staged-queue.json")


def _read() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict) and i.get("id")]


def _write(items: list[dict]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def recover_interrupted() -> None:
    """An item left "running" by a process that died goes back to pending."""
    with _lock:
        items = _read()
        changed = False
        for item in items:
            if item.get("status") == "running":
                item["status"] = "pending"
                changed = True
        if changed:
            _write(items)


def public(item: dict) -> dict:
    """The item without its full tracklist (the page needs the count only)."""
    out = {k: v for k, v in item.items() if k != "tracks"}
    out["preview"] = [
        str(t.get("title") or "") for t in (item.get("tracks") or [])[:3]
    ]
    return out


def list_items() -> list[dict]:
    with _lock:
        return _read()


def get(item_id: str) -> dict | None:
    with _lock:
        return next((i for i in _read() if i["id"] == item_id), None)


def add(
    *,
    kind: str,
    title: str,
    tracks: list[dict],
    subtitle: str = "",
    cover: str = "",
    source_url: str = "",
    whole: bool = False,
) -> dict:
    if not tracks:
        raise ValueError("Nothing to queue: no tracks given")
    kind = kind if kind in KINDS else ("track" if len(tracks) == 1 else "album")
    item: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "title": (title or "").strip() or str(tracks[0].get("title") or "Untitled"),
        "subtitle": (subtitle or "").strip(),
        "cover": cover or "",
        "source_url": source_url or "",
        # Everything at the link, not a pick from it: Start then downloads the
        # collection URL, which keeps the album's own order and numbering.
        "whole": bool(whole),
        "tracks": tracks,
        "track_count": len(tracks),
        "status": "pending",
        "added_at": time.time(),
        "started_at": 0.0,
        "finished_at": 0.0,
        "error": "",
    }
    with _lock:
        items = _read()
        items.append(item)
        if len(items) > MAX_ITEMS:
            # Drop the oldest finished ones first; pending work is never lost.
            finished = [i for i in items if i.get("status") in ("done", "failed")]
            for old in finished[: len(items) - MAX_ITEMS]:
                items.remove(old)
        _write(items)
    return item


def kind_for_url(url: str) -> str:
    """track / album / playlist / artist, as the GUI's detectUrlType() says."""
    u = (url or "").lower()
    if "spotify:track:" in u or "/track/" in u or "watch?v=" in u or "youtu.be/" in u:
        return "track"
    if "spotify:album:" in u or "/album/" in u or ("playlist" in u and "olak5uy_" in u):
        return "album"
    if "spotify:playlist:" in u or "/playlist/" in u or "list=" in u:
        return "playlist"
    if "spotify:artist:" in u or "/artist/" in u:
        return "artist"
    return ""


def download_target(item: dict) -> str | list[str]:
    """What the CLI-style downloader (the TUI's runner) is handed for an item.

    The collection URL when the whole thing was queued, otherwise one link
    per track — the same rule core/tracklist.download_target() applies.
    """
    from types import SimpleNamespace

    from .tracklist import track_url

    if item.get("target"):
        return item["target"]
    source = item.get("source_url") or ""
    if item.get("whole") and source:
        return source
    urls = []
    for raw in item.get("tracks") or []:
        url = track_url(SimpleNamespace(**raw) if isinstance(raw, dict) else raw, source)
        if url:
            urls.append(url)
    return urls or source


def set_target(item_id: str, target: str | list[str]) -> None:
    """Record the links a TUI pick resolved to (see download_target())."""
    with _lock:
        items = _read()
        for item in items:
            if item["id"] == item_id:
                item["target"] = target
                _write(items)
                return


def set_status(item_id: str, status: str, error: str = "") -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    with _lock:
        items = _read()
        for item in items:
            if item["id"] == item_id:
                item["status"] = status
                item["error"] = error
                now = time.time()
                if status == "running":
                    item["started_at"] = now
                elif status in ("done", "failed"):
                    item["finished_at"] = now
                _write(items)
                return item
    return None


def remove(item_ids: list[str]) -> int:
    wanted = set(item_ids or [])
    with _lock:
        items = _read()
        # A running item is mid-download: removing it would orphan the batch.
        kept = [i for i in items if i["id"] not in wanted or i.get("status") == "running"]
        _write(kept)
        return len(items) - len(kept)


def clear(scope: str = "all") -> int:
    """scope: "all" (everything not running) or "finished" (done + failed)."""
    with _lock:
        items = _read()
        if scope == "finished":
            kept = [i for i in items if i.get("status") not in ("done", "failed")]
        else:
            kept = [i for i in items if i.get("status") == "running"]
        _write(kept)
        return len(items) - len(kept)
