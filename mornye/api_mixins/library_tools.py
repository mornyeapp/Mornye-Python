"""api_mixins/library_tools.py — tools for files already on disk.

Three surfaces, all reached from the "Fix Local Files" page:

* a manual tag editor (`read_file_tags` / `save_file_tags`),
* an audio analysis card with a spectrogram (`analyze_audio_file`),
* a library reorganiser (`plan_library_reorganize` / `apply_library_reorganize`).

Every path the frontend sends is checked against the same approved roots the
local tagger uses (the download folder and the home folder).
"""

from __future__ import annotations

import base64
import binascii
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.loop_runner import run_sync


class LibraryToolsMixin:
    if TYPE_CHECKING:
        download_dir: str
        log: Any
        _push: Any

    def _is_approved_path(self, candidate: str) -> bool:
        try:
            resolved = Path(candidate).resolve()
        except Exception:
            return False
        roots = (Path(self.download_dir).resolve(), Path.home().resolve())
        return any(resolved.is_relative_to(root) for root in roots)

    # ── Tag editor ──────────────────────────────────────────────────────────

    def read_file_tags(self, path: str) -> dict:
        """The editable tags, lyrics and cover (as a data URL) of one file."""
        from ..core.tag_editor import EDITABLE_FIELDS, read_file_tags

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            data = read_file_tags(path)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        cover = ""
        if data.cover:
            b64 = base64.b64encode(data.cover).decode("ascii")
            cover = f"data:{data.cover_mime};base64,{b64}"
        return {
            "status": "ok",
            "fields": list(EDITABLE_FIELDS),
            "tags": data.tags,
            "lyrics": data.lyrics,
            "cover": cover,
        }

    def save_file_tags(
        self,
        path: str,
        edits: dict,
        lyrics: str | None = None,
        cover_data_url: str = "",
        remove_cover: bool = False,
    ) -> dict:
        """Writes the edited fields (empty value = remove the tag). `lyrics`
        None keeps the lyrics; `cover_data_url` replaces the artwork."""
        from ..core.tag_editor import save_file_tags_async

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        cover = None
        if cover_data_url:
            try:
                cover = base64.b64decode(
                    cover_data_url.split(",", 1)[-1], validate=True
                )
            except (binascii.Error, ValueError):
                return {"status": "error", "error": "The cover image is not valid"}
        try:
            run_sync(
                save_file_tags_async(
                    path,
                    edits or {},
                    lyrics=lyrics,
                    cover=cover,
                    remove_cover=bool(remove_cover),
                ),
            )
        except Exception as e:
            self.log(f"[tag-editor] {e}", "error")
            return {"status": "error", "error": str(e)}
        return {"status": "ok"}

    # ── Audio analysis ──────────────────────────────────────────────────────

    def analyze_audio_file(self, path: str) -> dict:
        """Technical facts about one file plus its spectrogram (PNG data URL)."""
        from ..core.audio_analysis import probe_audio, spectrogram_png_async

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            info = probe_audio(path)
            png = run_sync(spectrogram_png_async(path), timeout=180)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        b64 = base64.b64encode(png).decode("ascii")
        return {
            "status": "ok",
            "info": info,
            "spectrogram": f"data:image/png;base64,{b64}",
        }

    # ── Library reorganiser ─────────────────────────────────────────────────

    def get_reorganize_defaults(self) -> dict:
        from ..core.library_reorganize import DEFAULT_TEMPLATE, PLACEHOLDERS

        return {"template": DEFAULT_TEMPLATE, "placeholders": list(PLACEHOLDERS)}

    def plan_library_reorganize(self, root: str, template: str) -> dict:
        """Dry run: the moves that would bring `root` in line with `template`."""
        from ..core.library_reorganize import plan_reorganize

        if not self._is_approved_path(root):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            moves = plan_reorganize(root, template)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "moves": moves}

    def apply_library_reorganize(self, root: str, moves: list) -> dict:
        """Performs the moves of a plan (only the entries marked "move")."""
        from ..core.library_reorganize import apply_reorganize

        if not self._is_approved_path(root):
            return {"status": "error", "error": "Path is outside approved directories"}
        todo = [m for m in (moves or []) if m.get("status") == "move"]
        try:
            results = apply_reorganize(root, todo)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "results": results}

    # ── Player ──────────────────────────────────────────────────────────────

    def _is_media_path(self, candidate: str | Path) -> bool:
        """An approved music file, or one of the player's own lossless copies."""
        from ..core.paths import cache_path

        try:
            resolved = Path(candidate).resolve()
            if resolved.is_relative_to(cache_path("playback").resolve()):
                return True
        except Exception:
            return False
        return self._is_approved_path(str(candidate))

    def prepare_playback(self, path: str, local_url: bool = True) -> dict:
        """Gets `path` ready to play without lowering its quality.

        Files the engine can decode are streamed untouched; the rest are
        decoded once to a lossless FLAC copy (see core/playback.py). Returns
        the file to stream, a label saying what is being heard, and — for the
        desktop window, which has no route to the file — a URL on the local
        media server. The web UI streams `path` through /api/media instead.
        """
        from ..core.audio_analysis import probe_audio
        from ..core.media_server import get_media_server
        from ..core.playback import playable_file_async, quality_label

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            served, mode = run_sync(playable_file_async(path), timeout=900)
            info = probe_audio(path)
            label = quality_label(info, mode, Path(path).suffix)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        url = ""
        if local_url:
            url = get_media_server(self._is_media_path).url_for(served)
        return {
            "status": "ok",
            "path": str(served),
            "url": url,
            "mode": mode,
            "quality": label,
            **self._now_playing(path, info),
        }

    def _now_playing(self, path: str, info: dict) -> dict:
        """What the full-screen player shows: tags, cover and lyrics.

        Lyrics come from a ``.lrc`` next to the file when there is one (that is
        where synced lyrics are saved), else from the file's own lyrics tag.
        Never fails: a file with unreadable tags simply plays without them.
        """
        from ..core.tag_editor import read_file_tags

        out: dict = {
            "info": info,
            "title": "",
            "artist": "",
            "album": "",
            "cover": "",
            "lyrics": "",
            "explicit": False,
        }
        try:
            data = read_file_tags(path)
        except Exception:
            data = None
        if data is not None:
            tags = data.tags
            out.update(
                title=tags.get("TITLE", ""),
                artist=tags.get("ARTIST", ""),
                album=tags.get("ALBUM", ""),
                lyrics=data.lyrics,
                explicit=str(tags.get("ITUNESADVISORY", "")).strip() in ("1", "2"),
            )
            if data.cover:
                b64 = base64.b64encode(data.cover).decode("ascii")
                out["cover"] = f"data:{data.cover_mime};base64,{b64}"
        sidecar = Path(path).with_suffix(".lrc")
        try:
            if sidecar.is_file() and sidecar.stat().st_size < 2_000_000:
                out["lyrics"] = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return out

    # ── Player library ──────────────────────────────────────────────────────

    _library_scan_id = 0
    _library_thumb_cache: dict = {}

    def get_library_default_folder(self) -> dict:
        """Where the Player starts: the download folder."""
        return {"path": self.download_dir}

    def _scan_music_library_thread(self, scan_id: int, root: str) -> None:
        from ..core.library_scan import iter_audio_files, read_track_row

        try:
            files = list(iter_audio_files(root))
            total = len(files)
            batch: list = []
            done = 0
            for path in files:
                if scan_id != self._library_scan_id:
                    return  # a newer scan replaced this one
                batch.append(read_track_row(path))
                done += 1
                if len(batch) >= 60:
                    self._push(
                        "app_library_scan_chunk",
                        {"scan": scan_id, "items": batch, "done": done, "total": total},
                    )
                    batch = []
            if scan_id != self._library_scan_id:
                return
            self._push(
                "app_library_scan_chunk",
                {"scan": scan_id, "items": batch, "done": done, "total": total},
            )
            self._push(
                "app_library_scan_finished",
                {"scan": scan_id, "total": total, "folder": root},
            )
        except Exception as e:
            self.log(f"[player] scan failed: {e}", "error")
            self._push("app_library_scan_error", {"scan": scan_id, "error": str(e)})

    def scan_music_library(self, path: str) -> dict:
        """Lists the audio files under `path` for the Player, in the
        background: rows arrive via 'app_library_scan_chunk', the end via
        'app_library_scan_finished'. Starting a scan cancels the previous one."""
        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        if not Path(path).exists():
            return {"status": "error", "error": "That folder does not exist"}
        self._library_scan_id += 1
        scan_id = self._library_scan_id
        threading.Thread(
            target=self._scan_music_library_thread,
            args=(scan_id, str(path)),
            daemon=True,
        ).start()
        return {"status": "started", "scan": scan_id}

    def get_track_thumb(self, path: str) -> str:
        """A small cover for the Player's list as a data URL, "" if none."""
        from ..core.library_scan import thumbnail_jpeg

        if not self._is_approved_path(path):
            return ""
        cache = self._library_thumb_cache
        if path in cache:
            return cache[path]
        try:
            raw = thumbnail_jpeg(path)
        except Exception:
            raw = None
        url = (
            "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            if raw
            else ""
        )
        if len(cache) > 4000:
            cache.clear()
        cache[path] = url
        return url
