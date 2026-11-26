"""api_mixins/library_tools.py — tools for files already on disk.

Three surfaces, all reached from the "Fix Local Files" page:

* a manual tag editor (`read_file_tags` / `save_file_tags`),
* an audio analysis card with a spectrogram (`analyze_audio_file`),
* a library reorganiser (`plan_library_reorganize` / `apply_library_reorganize`).

Every path the frontend sends is checked against the same approved roots the
local tagger uses (the download folder, the home folder and the mounted
network shares).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.loop_runner import run_sync

#: The mounted shares, read at most this often. Reading them runs `mount`
#: on macOS, and the Player checks a path for every cover it draws.
_NETWORK_ROOTS_TTL_S = 15.0
_network_roots_state: dict[str, Any] = {"at": 0.0, "roots": []}
_network_roots_lock = threading.Lock()


def _network_roots_cached() -> list[Path]:
    with _network_roots_lock:
        if time.monotonic() - _network_roots_state["at"] > _NETWORK_ROOTS_TTL_S:
            try:
                from ..core.network_paths import network_roots

                roots = [root.resolve() for root in network_roots()]
            except Exception:
                roots = []
            _network_roots_state.update(at=time.monotonic(), roots=roots)
        return list(_network_roots_state["roots"])


#: Covers already shrunk for the Player, kept between launches: reading one
#: from a network share costs a round trip or more, so a library on a NAS
#: would otherwise be read again on every start.
_THUMB_DIR_NAME = "thumbs"


def _thumb_cache_file(path: str, px: int) -> Path | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    from ..core.paths import data_path

    digest = hashlib.sha1(
        f"{path}\0{st.st_mtime_ns}\0{st.st_size}\0{px}".encode("utf-8", "surrogatepass")
    ).hexdigest()
    return data_path(_THUMB_DIR_NAME, digest[:2], digest + ".jpg")


def _cached_thumbnail(path: str, px: int) -> bytes | None:
    """The shrunk cover of `path`, from disk when it was made before.

    An empty file records "no cover", so a file without one is not read
    again either. Editing the file changes its mtime/size and so the key.
    """
    from ..core.library_scan import thumbnail_jpeg

    target = _thumb_cache_file(path, px)
    if target is not None:
        try:
            return target.read_bytes() or None
        except OSError:
            pass
    try:
        raw = thumbnail_jpeg(path, px)
    except Exception:
        raw = None
    if target is not None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(raw or b"")
            os.replace(tmp, target)
        except OSError:
            pass
    return raw


class LibraryToolsMixin:
    if TYPE_CHECKING:
        download_dir: str
        log: Any
        _push: Any

    def _is_approved_path(self, candidate: str) -> bool:
        """Inside the download folder, the home folder, or a mounted network
        share: mounting a share is the user granting access to it, the same
        rule Fix Local Files and the duplicate scan apply."""
        try:
            resolved = Path(candidate).resolve()
        except Exception:
            return False
        local = (Path(self.download_dir).resolve(), Path.home().resolve())
        if any(resolved.is_relative_to(root) for root in local):
            return True
        # Windows opens \\server\share without mounting it, so a UNC path has
        # no mount point to compare with: allowed as Fix Local Files and the
        # duplicate scan already allow it (api_mixins/dedup.py).
        from ..core.network_paths import is_unc_path

        if is_unc_path(str(candidate)):
            return True
        return any(resolved.is_relative_to(root) for root in _network_roots_cached())

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

    def fetch_file_lyrics(
        self, path: str, tags: dict | None = None, provider: str = ""
    ) -> dict:
        """Lyrics for the file at `path`, looked up again: on `provider` alone
        when one is named, else with the lyrics services set in Settings, in
        their order. `tags` are the editor's current fields
        (TITLE, ARTIST, ALBUM, ISRC) so a corrected title is searched as
        typed; anything missing comes from the file. Nothing is written:
        the editor shows the text and Save puts it in the file."""
        from ..core.lyrics import (
            LYRICS_PROVIDER_LABELS,
            fetch_lyrics_async,
            lyrics_provider_label,
        )
        from ..core.tag_editor import read_file_tags

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            stored = read_file_tags(path).tags
        except Exception as e:
            return {"status": "error", "error": str(e)}
        fields = {**stored, **{k: v for k, v in (tags or {}).items() if v}}
        title = (fields.get("TITLE") or "").strip()
        artist = (fields.get("ARTIST") or fields.get("ALBUMARTIST") or "").strip()
        if not title or not artist:
            return {
                "status": "error",
                "error": "The file needs a title and an artist to search lyrics.",
            }
        try:
            import mutagen

            audio = mutagen.File(path)
            duration_s = int(audio.info.length) if audio and audio.info else 0
        except Exception:
            duration_s = 0
        load_settings = getattr(self, "load_settings", None)
        settings = (load_settings() if callable(load_settings) else None) or {}
        provider = (provider or "").strip()
        if provider and not (
            provider.startswith("ext:") or provider in LYRICS_PROVIDER_LABELS
        ):
            return {"status": "error", "error": f"Unknown lyrics service: {provider}"}
        providers = [provider] if provider else settings.get("lyrics_providers") or None
        try:
            text, found_on = run_sync(
                fetch_lyrics_async(
                    track_name=title,
                    artist_name=artist,
                    album_name=(fields.get("ALBUM") or "").strip(),
                    duration_s=duration_s,
                    isrc=(fields.get("ISRC") or "").strip(),
                    providers=providers,
                    apple_word_by_word=settings.get("apple_lyrics_word_by_word", True),
                ),
                timeout=120,
            )
        except Exception as e:
            return {"status": "error", "error": str(e)}
        if not text:
            where = f" on {lyrics_provider_label(provider)}" if provider else ""
            return {
                "status": "error",
                "error": f"No lyrics found{where} for this track.",
            }
        return {
            "status": "ok",
            "lyrics": text,
            "provider": lyrics_provider_label(found_on),
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

    def probe_audio_quality(self, path: str) -> dict:
        """Codec, bit depth and sample rate of one downloaded file, without
        the spectrogram: the album card's quality readout reads the first
        finished track of the album it shows."""
        from ..core.audio_analysis import probe_audio

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            return {"status": "ok", "info": probe_audio(path)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

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

    def prepare_playback(
        self, path: str, local_url: bool = True, native: bool = False
    ) -> dict:
        """Gets `path` ready to play without lowering its quality.

        Files the engine can decode are streamed untouched; the rest are
        decoded once to a lossless FLAC copy (see core/playback.py). Returns
        the file to stream, a label saying what is being heard, and — for the
        desktop window, which has no route to the file — a URL on the local
        media server. The web UI streams `path` through /api/media instead.

        `native`: the desktop's native engine (libmpv) plays the file, which
        decodes everything ffmpeg does, so no copy is made and no URL is
        needed; only the label and the Now Playing data are prepared.
        """
        from ..core.audio_analysis import probe_audio
        from ..core.media_server import get_media_server
        from ..core.playback import playable_file_async, quality_label

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        try:
            if native:
                served, mode = Path(path), "original"
            else:
                served, mode = run_sync(playable_file_async(path), timeout=900)
            info = probe_audio(path)
            label = quality_label(info, mode, Path(path).suffix)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        url = ""
        if local_url and not native:
            url = get_media_server(self._is_media_path).url_for(served)
        motion = self._animated_cover(path)
        canvas = self._track_canvas(path)
        motion_url = canvas_url = ""
        if local_url:
            # Through the media server even with the native engine playing
            # the audio: the window still has no other route to the files.
            server = get_media_server(self._is_media_path)
            motion_url = server.url_for(motion) if motion else ""
            canvas_url = server.url_for(canvas) if canvas else ""
        return {
            "status": "ok",
            "path": str(served),
            "url": url,
            "mode": mode,
            "quality": label,
            "motion_path": str(motion) if motion else "",
            "motion_url": motion_url,
            "canvas_path": str(canvas) if canvas else "",
            "canvas_url": canvas_url,
            **self._now_playing(path, info),
        }

    def _track_canvas(self, path: str) -> Path | None:
        """The track's Spotify Canvas, saved beside it under the same name
        (``Song.mp4`` for ``Song.flac``, see download_engine
        _write_canvas_sidecars_async), for the full-screen player to loop
        behind the lyrics. Videos only: a still canvas adds nothing to the
        cover. None when the track has none."""
        from ..core.canvas import _VIDEO_SUFFIXES

        audio = Path(path)
        for suffix in _VIDEO_SUFFIXES:
            candidate = audio.with_suffix(suffix)
            if candidate == audio:
                continue
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                continue
        return None

    def _animated_cover(self, path: str) -> Path | None:
        """The album's animated artwork, saved as ``cover.webp`` in the
        track's folder by the download (post_processing.
        _write_animated_artwork_sidecar), for the full-screen player to show
        in place of the still cover. None when the folder has none."""
        candidate = Path(path).parent / "cover.webp"
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            pass
        return None

    def _now_playing(self, path: str, info: dict) -> dict:
        """What the full-screen player shows: tags, cover and lyrics.

        Lyrics come from a ``.mlrc`` or ``.lrc`` next to the file when there is
        one (that is where synced lyrics and translations are saved), else
        from the file's own lyrics tag.
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
        # A .mlrc (Mornye's lyrics file, with translations) before a .lrc.
        from ..core.lyric_tags import sidecar_lyrics

        sidecar = sidecar_lyrics(path)
        if sidecar.strip():
            out["lyrics"] = sidecar
        return out

    # ── Player library ──────────────────────────────────────────────────────

    _library_scan_id = 0
    _library_thumb_cache: dict = {}
    #: Seconds between two "app_library_scan_progress" events: often enough
    #: for the bar to move, rarely enough not to flood the window or socket.
    _library_progress_interval = 0.25

    def get_library_default_folder(self) -> dict:
        """Where the Player starts: the download folder."""
        return {"path": self.download_dir}

    def _scan_music_library_thread(self, scan_id: int, roots: list[str]) -> None:
        """Fills the Player, then keeps it in step with the disk.

        A library seen before (core/player_library.py) is sent whole at once,
        from the database, and the Player is usable straight away; the walk
        that follows reads the tags only of files that are new or changed
        since, and sends just the difference ("app_library_scan_delta",
        then "app_library_scan_synced"). A library never seen before is read
        and sent file by file, as it always was, and stored on the way.
        """
        from ..core import db, player_library
        from ..core.library_scan import iter_audio_files, read_track_row

        def current() -> bool:
            return scan_id == self._library_scan_id  # a newer scan replaces this

        class Cancelled(Exception):
            """Stops the folder listing, which has no loop of its own here."""

        last_progress = [0.0]

        def progress(phase: str, done: int, total: int, force: bool = False) -> None:
            """Where the walk is, for the bar under the library summary:
            "listing" counts the audio files found so far (the total is not
            known yet: an indeterminate bar), "reading" is `done` of
            `total` files checked. Sent at most every
            _library_progress_interval seconds unless `force`."""
            if not current():
                raise Cancelled
            now = time.monotonic()
            if not force and now - last_progress[0] < self._library_progress_interval:
                return
            last_progress[0] = now
            self._push(
                "app_library_scan_progress",
                {"scan": scan_id, "phase": phase, "done": done, "total": total},
            )

        to_store: list = []
        try:
            stored = player_library.stored_rows(roots)
            if stored:
                rows = sorted(
                    (player_library.with_added(s) for s in stored.values()),
                    key=lambda r: str(r.get("path", "")).lower(),
                )
                for start in range(0, len(rows), 500):
                    if not current():
                        return
                    self._push(
                        "app_library_scan_chunk",
                        {
                            "scan": scan_id,
                            "items": rows[start : start + 500],
                            "done": min(start + 500, len(rows)),
                            "total": len(rows),
                        },
                    )
                self._push(
                    "app_library_scan_finished",
                    {
                        "scan": scan_id,
                        "total": len(rows),
                        "folder": roots[0],
                        "folders": roots,
                        "stored": True,
                    },
                )

            # Every folder of the library, a file listed once even when one
            # folder sits inside another.
            progress("listing", 0, 0, force=True)
            listed: list = []
            offset = 0  # files listed in the folders before this one

            def found(n: int) -> None:
                progress("listing", offset + n, 0)

            for root in roots:
                if not current():
                    return
                offset = len(listed)
                listed.extend(iter_audio_files(root, on_found=found))
            files = list(dict.fromkeys(listed))
            total = len(files)
            progress("reading", 0, total, force=True)
            batch: list = []
            to_store = []
            done = 0
            for path in files:
                done += 1
                progress("reading", done, total)
                if not current():
                    raise Cancelled
                try:
                    st = path.stat()
                except OSError:
                    continue
                known = stored.get(str(path))
                # A row stored before it carried a genre is read once more.
                if (
                    known is not None
                    and known.matches(st.st_mtime_ns, st.st_size)
                    and "genre" in known.row
                ):
                    continue
                row = {**read_track_row(path), "added": player_library.added_at(st)}
                batch.append(row)
                to_store.append((str(path), st.st_mtime_ns, st.st_size, row))
                if len(to_store) >= 200:
                    player_library.store(to_store)
                    to_store = []
                if len(batch) >= 60:
                    self._push(
                        (
                            "app_library_scan_delta"
                            if stored
                            else "app_library_scan_chunk"
                        ),
                        {"scan": scan_id, "items": batch, "done": done, "total": total},
                    )
                    batch = []
            player_library.store(to_store)
            if not current():
                return
            progress("reading", done, total, force=True)
            # A walk that found nothing at all is a share that stopped
            # answering, not a library emptied: the stored rows stay.
            gone = sorted(set(stored) - {str(p) for p in files}) if files else []
            player_library.forget(gone)
            if stored:
                self._push(
                    "app_library_scan_delta",
                    {"scan": scan_id, "items": batch, "removed": gone},
                )
                self._push("app_library_scan_synced", {"scan": scan_id, "total": total})
            else:
                self._push(
                    "app_library_scan_chunk",
                    {"scan": scan_id, "items": batch, "done": done, "total": total},
                )
                self._push(
                    "app_library_scan_finished",
                    {
                        "scan": scan_id,
                        "total": total,
                        "folder": roots[0],
                        "folders": roots,
                    },
                )
        except Cancelled:
            # cancel_music_library_scan has already told the Player; what was
            # read so far stays stored, so the next scan starts from there.
            if to_store:
                player_library.store(to_store)
        except Exception as e:
            self.log(f"[player] scan failed: {e}", "error")
            self._push("app_library_scan_error", {"scan": scan_id, "error": str(e)})
        finally:
            db.close_thread_connection()

    def scan_music_library(self, path: str | list[str]) -> dict:
        """Lists the audio files under `path` for the Player, in the
        background: rows arrive via 'app_library_scan_chunk', the end via
        'app_library_scan_finished'. Starting a scan cancels the previous one.

        `path` is one folder or a list of them (the library's folders). A
        network share may be named by its address — ``smb://nas/Music``,
        ``\\\\nas\\Music`` — once the OS has mounted it; it is mapped to the
        mounted folder (core/network_paths.py)."""
        from ..core.network_paths import resolve_library_path

        raw = [path] if isinstance(path, str) else list(path or [])
        roots: list[str] = []
        for item in raw:
            local, error = resolve_library_path(str(item))
            if error:
                return {"status": "error", "error": error}
            if not self._is_approved_path(local):
                return {
                    "status": "error",
                    "error": f"{item} is outside approved directories",
                }
            if not Path(local).is_dir():
                return {"status": "error", "error": f"{item} is not a folder"}
            if local not in roots:
                roots.append(local)
        if not roots:
            return {"status": "error", "error": "Add a music folder first"}
        self._library_scan_id += 1
        scan_id = self._library_scan_id
        threading.Thread(
            target=self._scan_music_library_thread,
            args=(scan_id, roots),
            daemon=True,
        ).start()
        return {"status": "started", "scan": scan_id, "folders": roots}

    def cancel_music_library_scan(self) -> dict:
        """Stops the Player's scan in progress, even mid-listing of a slow
        network share (it stops at the next file found). Rows read so far
        stay stored and shown; the Player hears 'app_library_scan_cancelled'.
        """
        scan_id = self._library_scan_id
        self._library_scan_id += 1  # the running scan is no longer current
        self._push("app_library_scan_cancelled", {"scan": scan_id})
        return {"status": "ok", "scan": scan_id}

    def get_track_thumb(self, path: str, size: int = 0) -> str:
        """A cover for the Player as a data URL, "" if none.

        `size` is the longest side in pixels: 0 keeps the small list
        thumbnail; the album grid asks for a larger one, clamped to 32–512.
        """
        from ..core.library_scan import THUMB_PX

        if not self._is_approved_path(path):
            return ""
        try:
            px = int(size or 0)
        except (TypeError, ValueError):
            px = 0
        px = THUMB_PX if px <= 0 else max(32, min(512, px))
        key = path if px == THUMB_PX else f"{path}\0{px}"
        cache = self._library_thumb_cache
        if key in cache:
            return cache[key]
        raw = _cached_thumbnail(path, px)
        url = (
            "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            if raw
            else ""
        )
        if len(cache) > 4000:
            cache.clear()
        cache[key] = url
        return url

    def get_track_thumbs(self, paths: list, size: int = 0) -> dict:
        """Several Player covers in one call, read side by side.

        A cover on a network share takes a round trip or more to read, so
        the rows on screen are fetched together rather than one bridge call
        each. Returns {path: data URL or ""}.
        """
        wanted = [p for p in dict.fromkeys(paths or []) if isinstance(p, str) and p][
            :64
        ]
        if not wanted:
            return {}
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
            urls = list(pool.map(lambda p: self.get_track_thumb(p, size), wanted))
        return dict(zip(wanted, urls, strict=True))

    def get_artist_photos(self, names: list) -> dict:
        """The artists' own photos for the Player's artist cards and header:
        {name: image URL or ""}, from the same Spotify artist search the
        text search uses, cached on disk (core/artist_photos.py). "" means
        the card keeps the album cover it already shows.
        """
        from ..core.artist_photos import artist_photos

        try:
            return artist_photos(names if isinstance(names, list) else [])
        except Exception as exc:
            self.log(f"get_artist_photos error: {exc}", "error")
            return {}
