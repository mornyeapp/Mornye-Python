"""api_mixins/queue_history.py — the Queue and History pages.

Queue: the staged queue (core/staged_queue.py). "Add to queue" writes the
selected tracks down in full; Start / Start all hands each item to the same
self-contained download job a retry or a --web download uses
(`run_download_job`), one item after another, so two staged albums never
race each other for the providers.

History: two lists that already existed, made browsable — what was
downloaded (core/download_log.py) and what was opened (the URL history the
Home page's "Recent fetches" shows).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any


class QueueHistoryMixin:
    if TYPE_CHECKING:
        current_tracks: Any
        current_url: Any
        _download_queue: Any
        _submit_download_job: Any
        run_download_job: Any
        log: Any
        _push: Any

    _staged_runner: threading.Thread | None = None

    # ── internals ──────────────────────────────────────────────────────

    def _owner(self) -> str:
        return getattr(self, "owner", "") or ""

    def _staged_changed(self) -> None:
        try:
            self._push("app_staged_queue_changed", self.get_staged_queue())
        except Exception:
            pass

    # ── Queue ──────────────────────────────────────────────────────────

    def get_staged_queue(self) -> dict:
        from ..core import staged_queue

        items = [staged_queue.public(i) for i in staged_queue.list_items()]
        counts = {k: 0 for k in staged_queue.KINDS}
        for item in items:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        running = self._staged_runner is not None and self._staged_runner.is_alive()
        return {"items": items, "counts": counts, "running": running}

    def add_to_staged_queue(self, selected_indices, info: dict | None = None) -> dict:
        """Write the selected tracks of the current tracklist down for later.

        `info` carries what the page knows and the tracklist does not: the
        kind (track/album/playlist/artist), the title and subtitle shown on
        the card, and the cover.
        """
        from ..core import staged_queue

        info = dict(info or {})
        try:
            indices = [int(i) for i in selected_indices or []]
            tracks = [self.current_tracks[i].model_dump(mode="json") for i in indices]
        except (AttributeError, IndexError, TypeError, ValueError):
            return {
                "ok": False,
                "error": "Those tracks are no longer loaded; fetch the link again.",
            }
        if not tracks:
            return {"ok": False, "error": "Select at least one track to queue."}
        try:
            item = staged_queue.add(
                kind=str(info.get("kind") or ""),
                title=str(info.get("title") or ""),
                subtitle=str(info.get("subtitle") or ""),
                cover=str(info.get("cover") or ""),
                source_url=str(info.get("source_url") or self.current_url or ""),
                tracks=tracks,
                whole=len(tracks) == len(self.current_tracks or []),
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        self._staged_changed()
        return {"ok": True, "item": staged_queue.public(item)}

    def remove_staged_items(self, ids) -> dict:
        from ..core import staged_queue

        removed = staged_queue.remove([str(i) for i in ids or []])
        self._staged_changed()
        return {"ok": True, "removed": removed}

    def clear_staged_queue(self, scope: str = "all") -> dict:
        from ..core import staged_queue

        removed = staged_queue.clear("finished" if scope == "finished" else "all")
        self._staged_changed()
        return {"ok": True, "removed": removed}

    def retry_staged_items(self, ids) -> dict:
        """Failed or finished items back to pending, so Start picks them up."""
        from ..core import staged_queue

        for item_id in ids or []:
            item = staged_queue.get(str(item_id))
            if item and item.get("status") in ("failed", "done"):
                staged_queue.set_status(item["id"], "pending")
        self._staged_changed()
        return {"ok": True}

    def start_staged_items(self, ids=None, config: dict | None = None) -> dict:
        """Start the given pending items (all pending ones when `ids` is empty).

        One runner thread works through them in order; pressing Start again
        while it runs only adds to what it will pick up next.
        """
        from ..core import staged_queue

        wanted = {str(i) for i in ids or []}
        pending = [
            i
            for i in staged_queue.list_items()
            if i.get("status") == "pending" and (not wanted or i["id"] in wanted)
        ]
        if not pending:
            return {"ok": False, "error": "Nothing waiting to start."}
        self._staged_config = dict(config or {})
        self._staged_wanted = wanted
        if self._staged_runner is None or not self._staged_runner.is_alive():
            self._staged_runner = threading.Thread(target=self._run_staged, daemon=True)
            self._staged_runner.start()
        return {"ok": True, "started": len(pending)}

    def _run_staged(self) -> None:
        from ..core import staged_queue

        while True:
            wanted: set[str] = getattr(self, "_staged_wanted", set())
            item = next(
                (
                    i
                    for i in staged_queue.list_items()
                    if i.get("status") == "pending"
                    and (not wanted or i["id"] in wanted)
                ),
                None,
            )
            if item is None:
                break
            staged_queue.set_status(item["id"], "running")
            self._staged_changed()
            tracks = item.get("tracks") or []
            payload = {
                "indices": list(range(len(tracks))),
                "tracks": tracks,
                "source_url": item.get("source_url") or "",
                "whole": True,
                "config": dict(getattr(self, "_staged_config", {}) or {}),
                # Not this page's session: the live Downloading list is keyed
                # by the tracklist on screen, and these tracks are not on it.
                # It finishes as a background download instead.
                "session": "",
            }
            try:
                if self._download_queue is not None:
                    self._submit_download_job(payload)
                else:
                    self.run_download_job(payload)
                staged_queue.set_status(item["id"], "done")
                self.log(f"Queue: finished '{item.get('title')}'.", "ok")
            except Exception as e:  # one bad item must not stop the rest
                staged_queue.set_status(item["id"], "failed", str(e))
                self.log(f"Queue: '{item.get('title')}' failed: {e}", "error")
            self._staged_changed()
        self._staged_changed()

    # ── History ────────────────────────────────────────────────────────

    def get_download_history(
        self, query: str = "", sort: str = "newest", limit: int = 500
    ) -> dict:
        from ..core import download_log

        records = download_log.search(
            query, sort=sort, limit=limit, owner=self._owner()
        )
        return {
            "items": [r.to_dict() for r in records],
            "totals": download_log.totals(owner=self._owner()),
        }

    def remove_download_history(self, ids) -> dict:
        from ..core import download_log

        return {
            "ok": True,
            "removed": download_log.delete(list(ids or []), owner=self._owner()),
        }

    def clear_download_history(self) -> dict:
        from ..core import download_log

        return {"ok": True, "removed": download_log.clear(owner=self._owner())}

    def clear_fetch_history(self) -> dict:
        from ..core.loop_runner import run_sync
        from ..core.session_memory import clear_url_history_async

        try:
            run_sync(clear_url_history_async())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
