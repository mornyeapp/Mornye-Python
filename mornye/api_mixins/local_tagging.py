"""api_mixins/local_tagging.py — MornyeAPI's "Fix Local Files" surface.

Extracted verbatim from app.py (see api_mixins/__init__.py for why this is a
mixin rather than a standalone class): scan_local() and apply_local_tags()
are Phases 2 and 5 of the Local Tagging feature documented in the README
under "Local Tagging" — scanning a folder, matching each file against Spotify
metadata, and applying the chosen match with an automatic backup.
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any

from ..core.loop_runner import run_sync


class _ScanCancelled(Exception):
    """Stops the folder listing of a scan that was cancelled or replaced."""


class LocalTaggingMixin:
    if TYPE_CHECKING:
        download_dir: str
        load_settings: Any
        log: Any
        _push: Any
    # ── Local Auto-Tagger (Phase 5: GUI/Web) ────────────────────────────────

    def _serialize_scan_entry(self, entry) -> dict:
        from ..core import local_scan_cache

        info = entry.info
        candidates = [
            {
                "confidence": c.confidence,
                "is_safe": c.is_safe,
                # "isrc" when the file's own ISRC identified the recording,
                # "text" when it was scored on title/artist similarity. The
                # UI labels the two differently: one is identity, the other
                # is a guess, and the user deserves to know which.
                "how": c.how,
                "title_ratio": c.title_ratio,
                "variant_unconfirmed": c.variant_unconfirmed,
                "artist_known": c.artist_known,
                # first_artist is a computed property on TrackMetadata, not a
                # stored field — model_dump() only includes real fields, so
                # it has to be added back in explicitly or the frontend
                # (which reads best.metadata.first_artist) gets undefined.
                "metadata": {
                    **c.metadata.model_dump(),
                    "first_artist": c.metadata.first_artist,
                },
            }
            for c in entry.candidates
        ]
        return {
            "file_path": info.file_path,
            "old_title": info.old_title,
            "old_artist": info.old_artist,
            "old_album": info.old_album,
            "old_year": info.old_year,
            "old_genre": info.old_genre,
            "old_isrc": info.old_isrc,
            "old_duration_ms": info.old_duration_ms,
            # A thumbnail: the list draws it at 40 px, and the full cover
            # of every file made the results hundreds of MB.
            "old_cover_base64": local_scan_cache.cover_thumbnail(info.old_cover_base64),
            "has_tags": info.has_tags,
            "guessed_title": info.guessed_title,
            "guessed_artist": info.guessed_artist,
            "error": info.error,
            "candidates": candidates,
        }

    #: Bumped by every scan_local(): a scan still running for an older id
    #: stops between two batches.
    _local_scan_id = 0
    #: Seconds between two "app_local_scan_progress" events.
    _local_progress_interval = 0.25

    def _acoustid_key(self) -> str:
        # A user-supplied AcoustID key, when set, is used instead of the
        # shared one for identifying files the tags could not resolve —
        # see core/acoustid_lookup.py for why that matters.
        try:
            return str(self.load_settings().get("acoustid_api_key") or "")
        except Exception:
            return ""

    def _scan_local_thread(self, scan_id: int, path: str) -> None:
        """Fills the Fix Local Files list, from what is stored first.

        The results stored by an earlier scan of the folder
        (core/local_scan_cache.py) are sent at once ("app_local_scan_chunk",
        cached), and only the files that are new, changed or whose result
        has grown old are read and matched again, in batches sent as they
        are done. "app_local_scan_done" ends it, with the files that are
        gone. "app_local_scan_progress" says where the walk is.
        """
        from ..core import db
        from ..core import local_scan_cache as cache
        from ..core.local_processor import scan_and_match_files_async
        from ..core.local_scanner import list_audio_files

        def current() -> bool:
            return scan_id == self._local_scan_id

        last_progress = [0.0]

        def progress(phase: str, done: int, total: int, force: bool = False) -> None:
            if not current():
                raise _ScanCancelled  # also stops a long listing of a share
            now = time.monotonic()
            if not force and now - last_progress[0] < self._local_progress_interval:
                return
            last_progress[0] = now
            self._push(
                "app_local_scan_progress",
                {"scan": scan_id, "phase": phase, "done": done, "total": total},
            )

        def send(files: list[dict], cached: bool = False) -> None:
            self._push(
                "app_local_scan_chunk",
                {"scan": scan_id, "path": path, "files": files, "cached": cached},
            )

        try:
            now = time.time()
            stored = cache.stored(path)
            shown = sorted(
                (
                    s.entry
                    for s in stored.values()
                    if now - s.scanned_at < cache.MAX_AGE_S
                ),
                key=lambda e: str(e.get("file_path", "")),
            )
            for start in range(0, len(shown), 200):
                send(shown[start : start + 200], cached=True)

            progress("listing", 0, 0, force=True)
            files = list_audio_files(path, on_found=lambda n: progress("listing", n, 0))
            if not current():
                return
            todo: list[str] = []
            stats: dict[str, tuple[int, int]] = {}
            for f in files:
                if not current():
                    return
                try:
                    st = f.stat()
                except OSError:
                    continue
                known = stored.get(str(f))
                if known is not None and known.matches(st.st_mtime_ns, st.st_size, now):
                    continue
                todo.append(str(f))
                stats[str(f)] = (st.st_mtime_ns, st.st_size)
            # A listing that found nothing is a share that stopped
            # answering, not a folder emptied: the stored results stay.
            gone = sorted(set(stored) - {str(f) for f in files}) if files else []
            cache.forget(gone)

            done = [0]
            progress("reading", 0, len(todo), force=True)

            def on_batch(entries) -> None:
                payload = [self._serialize_scan_entry(e) for e in entries]
                cache.store(
                    (row["file_path"], *stats[row["file_path"]], row)
                    for row in payload
                    if row["file_path"] in stats
                )
                done[0] += len(payload)
                if current():
                    send(payload)
                    progress("reading", done[0], len(todo), force=True)

            run_sync(
                scan_and_match_files_async(
                    todo,
                    on_batch=on_batch,
                    acoustid_key=self._acoustid_key(),
                    cancelled=lambda: not current(),
                )
            )
            if not current():
                return
            self._push(
                "app_local_scan_done",
                {
                    "scan": scan_id,
                    "path": path,
                    "total": len(files) or len(shown),
                    "checked": len(todo),
                    "removed": gone,
                },
            )
        except _ScanCancelled:
            pass  # cancel_local_scan has told the page; what was done is stored
        except Exception as e:
            self.log(f"[local-tagger] scan failed: {e}", "error")
            self._push("app_local_scan_error", str(e))
        finally:
            db.close_thread_connection()

    def cancel_local_scan(self) -> dict:
        """Stops the Fix Local Files scan in progress: the folder listing at
        the next file found, the matching before its next batch or
        fingerprint. The results already worked out stay in the list and
        are stored, so scanning again picks up from there. The page hears
        'app_local_scan_cancelled'."""
        scan_id = self._local_scan_id
        self._local_scan_id += 1  # the running scan is no longer current
        self._push("app_local_scan_cancelled", {"scan": scan_id})
        return {"status": "ok", "scan": scan_id}

    def scan_local(self, path: str) -> dict:
        """Phase 5, Task 2: scans `path` (file or folder) and matches every
        track found, in a background thread. Results arrive via the
        'app_local_scan_results' push event — see _serialize_scan_entry()
        for the exact shape (a list of {file_path, old_*, candidates: [...]}).
        """
        if not path:
            return {"status": "error", "error": "No path given"}

        # Quotes stripped, ~ expanded, and a network share (smb://nas/Music,
        # \\nas\Music) mapped to where the OS mounted it.
        from ..core.network_paths import (
            is_unc_path,
            network_roots,
            resolve_library_path,
        )

        path, share_error = resolve_library_path(path)
        if share_error:
            return {"status": "error", "error": share_error}

        # Validate that the path exists
        if not os.path.exists(path):
            return {
                "status": "error",
                "error": f"Path does not exist: {path}",
            }

        # Path traversal protection
        from pathlib import Path

        try:
            resolved = Path(path).resolve()
            approved_roots = [
                Path(self.download_dir).resolve(),
                Path.home().resolve(),
                # Mounted network shares: mounting one is the user granting
                # access to it, the same as it being under their home.
                *(root.resolve() for root in network_roots()),
            ]
            is_safe = is_unc_path(path)
            for root in approved_roots:
                try:
                    resolved.relative_to(root)
                    is_safe = True
                    break
                except ValueError:
                    continue
            if not is_safe:
                return {
                    "status": "error",
                    "error": "Access denied: path is outside approved directories",
                }
        except Exception as e:
            return {
                "status": "error",
                "error": f"Path validation failed: {e}",
            }

        self._local_scan_id += 1
        scan_id = self._local_scan_id
        threading.Thread(
            target=self._scan_local_thread,
            args=(scan_id, path),
            daemon=True,
        ).start()
        return {"status": "started", "scan": scan_id}

    def _apply_local_tags_thread(self, items) -> None:
        try:
            from pathlib import Path

            from ..core.local_processor import (
                default_embed_options,
                retag_local_file_async,
            )
            from ..core.models import TrackMetadata

            opts = default_embed_options(
                artist_separator=self.load_settings().get("artist_separator") or None,
            )
            results = []
            total = len(items)

            # Approved roots for path traversal protection
            approved_roots = [
                Path(self.download_dir).resolve(),
                Path.home().resolve(),
            ]

            def _is_path_safe(candidate_path: str) -> bool:
                try:
                    resolved = Path(candidate_path).resolve()
                    for root in approved_roots:
                        try:
                            resolved.relative_to(root)
                            return True
                        except ValueError:
                            continue
                    return False
                except Exception:
                    return False

            for idx, item in enumerate(items, start=1):
                file_path = item.get("file_path", "")
                metadata_dict = item.get("metadata") or {}
                backup = item.get("backup", True)

                # Path traversal protection
                if not _is_path_safe(file_path):
                    results.append(
                        {
                            "file_path": file_path,
                            "success": False,
                            "error": "Access denied: path is outside approved directories",
                        }
                    )
                    self._push(
                        "app_local_apply_progress",
                        {"done": idx, "total": total, "last": results[-1]},
                    )
                    continue

                try:
                    metadata = TrackMetadata.model_validate(metadata_dict)
                    result = run_sync(
                        retag_local_file_async(
                            file_path, metadata, opts, backup=backup
                        ),
                    )
                    results.append(
                        {
                            "file_path": result.file_path,
                            "success": result.success,
                            "error": result.error,
                        },
                    )
                except Exception as e:
                    results.append(
                        {"file_path": file_path, "success": False, "error": str(e)},
                    )

                self._push(
                    "app_local_apply_progress",
                    {"done": idx, "total": total, "last": results[-1]},
                )

            self._push("app_local_apply_finished", {"results": results})
        except Exception as e:
            self.log(f"[local-tagger] apply failed: {e}", "error")
            self._push("app_local_apply_error", str(e))

    def apply_local_tags(self, items: list) -> dict:
        """Phase 5, Task 5: applies the chosen match for each item in
        `items` — a list of {file_path, metadata, backup?} dicts, where
        `metadata` is one of the `candidates[i].metadata` dicts a prior
        scan_local() call pushed. Runs in a background thread; per-file
        progress arrives via 'app_local_apply_progress', the final summary
        via 'app_local_apply_finished' (both pushed — see set_window()/
        webapp.py for how push reaches the frontend in each mode).
        """
        if not items:
            return {"status": "error", "error": "Nothing to apply"}
        threading.Thread(
            target=self._apply_local_tags_thread,
            args=(items,),
            daemon=True,
        ).start()
        return {"status": "started"}

    # ── Convert existing files to another format ────────────────────────────

    def get_convert_formats(self) -> list:
        """The target formats the Convert dialog offers (see
        core.transcode.conversion_targets())."""
        from ..core.transcode import conversion_targets

        return conversion_targets()

    def _convert_local_files_thread(self, paths: list, options: dict) -> None:
        try:
            from pathlib import Path

            from ..core.transcode import (
                ensure_ffmpeg_available,
                is_lossless,
                normalize_bitrate,
                source_is_lossless,
                transcode_file_async,
            )

            fmt = options["format"]
            bitrate = normalize_bitrate(options.get("bitrate"))
            keep_original = bool(options.get("keep_original", False))
            max_rate = int(options.get("max_sample_rate") or 0)
            max_depth = int(options.get("max_bit_depth") or 0)
            lossless_target = is_lossless(fmt)

            approved_roots = [
                Path(self.download_dir).resolve(),
                Path.home().resolve(),
            ]

            def _is_path_safe(candidate: str) -> bool:
                try:
                    resolved = Path(candidate).resolve()
                except Exception:
                    return False
                return any(resolved.is_relative_to(root) for root in approved_roots)

            ensure_ffmpeg_available(fmt)

            results = []
            total = len(paths)
            for idx, file_path in enumerate(paths, start=1):
                result: dict = {"file_path": file_path, "success": False}
                if not _is_path_safe(file_path):
                    result["error"] = (
                        "Access denied: path is outside approved directories"
                    )
                else:
                    try:
                        if lossless_target and not source_is_lossless(file_path):
                            # Same rule as the mobile app: a lossless copy of
                            # a lossy file is only a bigger file.
                            raise ValueError(
                                "Source is lossy: converting it to a lossless "
                                "format cannot restore quality",
                            )
                        dest = run_sync(
                            transcode_file_async(
                                file_path,
                                fmt=fmt,
                                bitrate=bitrate,
                                keep_original=keep_original,
                                max_sample_rate=max_rate if lossless_target else 0,
                                max_bit_depth=max_depth if lossless_target else 0,
                            ),
                        )
                        result.update(success=True, output_path=str(dest))
                    except Exception as e:
                        result["error"] = str(e)
                results.append(result)
                self._push(
                    "app_local_convert_progress",
                    {"done": idx, "total": total, "last": result},
                )

            self._push("app_local_convert_finished", {"results": results})
        except Exception as e:
            self.log(f"[convert] failed: {e}", "error")
            self._push("app_local_convert_error", str(e))

    def convert_local_files(self, paths: list, options: dict) -> dict:
        """Converts each file in `paths` to `options["format"]`, next to the
        original, keeping its tags and cover. `options`: format, bitrate
        (lossy targets), keep_original, max_sample_rate / max_bit_depth
        (lossless targets, 0 = keep the source's). Runs in a background
        thread; progress arrives via 'app_local_convert_progress', the summary
        via 'app_local_convert_finished'.
        """
        from ..core.transcode import normalize_transcode_format

        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return {"status": "error", "error": "Nothing to convert"}
        try:
            fmt = normalize_transcode_format((options or {}).get("format"))
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        if not fmt:
            return {"status": "error", "error": "Choose a target format"}
        threading.Thread(
            target=self._convert_local_files_thread,
            args=(paths, {**options, "format": fmt}),
            daemon=True,
        ).start()
        return {"status": "started"}
