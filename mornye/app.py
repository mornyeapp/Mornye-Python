from __future__ import annotations

from .core.env import env_value

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

import webview

from .api_mixins.covers_lyrics import CoversLyricsMixin
from .api_mixins.csv_import import CsvImportMixin
from .api_mixins.dedup import DedupMixin
from .api_mixins.queue_history import QueueHistoryMixin
from .api_mixins.discovery import DiscoveryMixin
from .api_mixins.extension_health import ExtensionHealthMixin
from .api_mixins.extension_settings import ExtensionSettingsMixin
from .api_mixins.failed_tracks import FailedTracksMixin
from .api_mixins.integrations import IntegrationsMixin
from .api_mixins.library_tools import LibraryToolsMixin
from .api_mixins.link import LinkMixin
from .api_mixins.local_tagging import LocalTaggingMixin
from .api_mixins.player_playlists import PlayerPlaylistsMixin
from .api_mixins.music_extras import MusicExtrasMixin
from .api_mixins.native_player import NativePlayerMixin
from .api_mixins.personal import PersonalMixin
from .api_mixins.cast import CastMixin
from .api_mixins.connect import ConnectMixin
from .api_mixins.storage import StorageMixin
from .api_mixins.search import SearchMixin
from .api_mixins.signed_sessions import SignedSessionsMixin
from .api_mixins.stats import StatsMixin
from .api_mixins.subscriptions import SubscriptionsMixin
from .api_mixins.trust import TrustMixin
from .core.atomic_io import write_json_atomic
from .core.http import AsyncHttpClient
from .core.loop_runner import run_sync
from .core.output_sink import CallbackLogHandler
from .core.paths import (
    cache_dir,
    cache_path,
    default_download_dir,
)
from .core.url_utils import url_host_matches

#: One definition, shared with the terminal UI via core/paths, so the two
#: frontends cannot default to different folders on the same machine.
DEFAULT_DOWNLOAD_DIR = default_download_dir()

# Opt-in required before the GUI/web bridge may run post_download_action
# ="command" (see _post_command_allowed / _download_task).
POST_COMMAND_ENV = "MORNYE_ALLOW_POST_COMMAND"

#: Playcount lookups run one request per track against Spotify's internal
#: GraphQL API. These two together cap that at ~12 requests/second — roughly
#: 2.5 minutes for a 1800-track CSV, in the background — instead of the ~32/s
#: an unspaced pool of 8 produced. See _fetch_track_playcounts().
PLAYCOUNT_WORKERS = 4
PLAYCOUNT_MIN_INTERVAL_S = 0.08


def _post_command_allowed() -> bool:
    """Whether this process will let a *bridge* caller run a shell command
    as a post-download action.

    Off by default, and deliberately not something the caller can turn on
    for itself. Every method on this class is reachable two ways: from the
    desktop window via pywebview, and — in `--web` mode — as an HTTP
    endpoint (see webapp.py's ALLOWED_METHODS), where the "caller" may be
    anyone who can reach the port and the config dict is just a JSON body.
    Reading a shell command out of that dict makes the two indistinguishable,
    so the decision is moved somewhere only whoever started the process can
    reach: this environment variable.

    The CLI is unaffected — `--post-action command` there goes straight to
    client.Mornye() without passing through this class, and someone who
    can already type a shell command into their own shell gains nothing by
    typing it into ours.
    """
    return (env_value(POST_COMMAND_ENV, "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )


#: "builtin" makes the desktop app's Choose Folder use the folder picker in
#: the page instead of the operating system's dialog.
FOLDER_DIALOG_ENV = "MORNYE_FOLDER_DIALOG"


def folder_dialog_is_builtin() -> bool:
    """Whether Choose Folder should open the picker drawn in the page.

    The native dialog is a second window, and some setups cannot show one:
    a kiosk compositor such as cage gives the screen to a single window, so
    the GTK dialog opened behind the app while the app, waiting on it, stopped
    answering. The page's own picker (the one the web build always uses)
    needs no second window. Off unless the environment asks for it, so the
    desktop app keeps its native dialog everywhere else.
    """
    return (env_value(FOLDER_DIALOG_ENV, "") or "").strip().lower() == "builtin"


#: Console formats launcher.py uses for the same records (see its
#: --log-level/--verbose plumbing). Reused verbatim so a GUI started from a
#: terminal reads exactly like `mornye <url>` does.
_CONSOLE_LOG_FORMAT = "%(levelname)s: %(message)s"
_VERBOSE_CONSOLE_LOG_FORMAT = "%(levelname)s:%(name)s: %(message)s"


#: Serialises every read and write of gui-settings.json. pywebview runs each
#: js_api call on its own thread, so at startup load_settings() and
#: save_theme()/save_accent() routinely overlap; together with the atomic
#: write in save_settings() this is what stops a reader from ever seeing a
#: half-written file ("Expecting value: line 1 column 1 (char 0)").
_SETTINGS_LOCK = threading.RLock()


def _settings_file() -> Path:
    return cache_path("gui-settings.json")


def _read_settings_file() -> dict:
    """gui-settings.json as stored ({} if absent). Raises if it is unreadable."""
    path = _settings_file()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data


def saved_log_level() -> int:
    """The level the Settings form last saved — "Standard (INFO)" by default.

    Read straight off gui-settings.json rather than through
    MornyeAPI.load_settings(), because the console has to be configured
    before the bridge object (and its logging handlers) exist.
    """
    with contextlib.suppress(Exception):
        cfg = json.loads(cache_path("gui-settings.json").read_text(encoding="utf-8"))
        if str(cfg.get("log_level", "")).strip().upper() == "DEBUG":
            return logging.DEBUG
    return logging.INFO


def configure_console_logging(level: int) -> None:
    """Configures the terminal the GUI (or the `--web` server) was started
    from exactly like the CLI configures its own.

    This used to be `basicConfig(level=WARNING, format=...)` while only the
    `Mornye` logger was raised to INFO by the download task. Two things
    followed, both of them visible in the terminal behind the window:

      * every provider fallback dumped a full multi-frame traceback, because
        plenty of call sites log an expected failure (a missing API config, a
        track not found) with exc_info=True, and only the CLI installed the
        _CleanConsoleFormatter that drops it below DEBUG;
      * httpx/asyncio and the extensions' own loggers were still judged
        against WARNING, so the two levels the Settings form offers — INFO
        and DEBUG — applied to nothing but our own logger, and the same
        records reached the root handler a second time as "INFO: ...".

    Mirroring launcher.py means the GUI honours "Standard (INFO)" the way
    `mornye --log-level INFO` does: one clean line per event, tracebacks
    only when the level really is DEBUG.
    """
    from .client import _CleanConsoleFormatter
    from .launcher import _quiet_noisy_libraries

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _CleanConsoleFormatter(
            _VERBOSE_CONSOLE_LOG_FORMAT
            if level <= logging.DEBUG
            else _CONSOLE_LOG_FORMAT
        )
    )
    # The tracebacks the console leaves out go to errors.log (ERROR only).
    from .core.error_log import error_log_handler

    handlers: list[logging.Handler] = [handler]
    file_handler = error_log_handler()
    if file_handler is not None:
        handlers.append(file_handler)
    # force=True: this also runs from _download_task when the picker changed
    # the level, and basicConfig is otherwise a no-op once root has handlers.
    # force also closes the errors.log handler the previous call installed.
    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("pywebview").setLevel(logging.WARNING)
    _quiet_noisy_libraries(level)


class UILogHandler(CallbackLogHandler):
    """Mirrors the `Mornye` logger into the GUI's Logs panel.

    Every record still reaches the panel; what changes with the level is
    whether it also raises a toast. INFO used to map to "info", which
    toasts — so a single album download popped one notification per
    library log line ("[utilities.spotify_metadata] [DEBUG] URL
    type: ...", "[ExtMgr] 'x' already up-to-date", one per merged
    segment, ...) and buried the screen in what read as debug spam.
    These are diagnostics, not things the user asked to be interrupted
    by, so INFO and DEBUG now map to "debug": panel-only. Only WARNING
    and above, which the user does need to see without opening the
    panel, still toast.

    The formatting, the severity buckets and the never-raise guarantee now
    live in `core.output_sink.CallbackLogHandler`, because the TUI needs the
    same three of them and a second copy would only be the one that drifts.
    """

    def __init__(self, api) -> None:
        super().__init__(api.log)
        self.api = api


# F6: failures that used to vanish behind `except Exception: pass`. Debug
# level: none of them is the user's to act on, but a --log-level DEBUG run
# shows them. Not under "mornye", whose UILogHandler pushes every record to
# the GUI: a failing push would log about itself through the push that fails.
_quiet_log = logging.getLogger("mornye_gui.app")
_push_guard = threading.local()


def _covers_whole_list(indices: list[int], listed: list) -> bool:
    """Whether `indices` select every track of `listed`.

    By the tracks themselves, not only their positions: a playlist can list
    the same song twice, and the Queue keeps one row per song, so its
    second place is never sent. Compared by position alone such a playlist
    was never whole, and downloaded as loose tracks: no folder, no M3U
    (issue #277). A track with no id counts by its position.
    """
    if sorted(indices) == list(range(len(listed))):
        return True
    if not listed or any(not 0 <= i < len(listed) for i in indices):
        return False

    def identity(position: int) -> object:
        track_id = getattr(listed[position], "id", None)
        return track_id if track_id else ("position", position)

    chosen = {identity(i) for i in indices}
    return chosen == {identity(i) for i in range(len(listed))}


class MornyeAPI(
    SearchMixin,
    LocalTaggingMixin,
    LibraryToolsMixin,
    PlayerPlaylistsMixin,
    MusicExtrasMixin,
    CoversLyricsMixin,
    DiscoveryMixin,
    DedupMixin,
    TrustMixin,
    SubscriptionsMixin,
    ExtensionHealthMixin,
    ExtensionSettingsMixin,
    CsvImportMixin,
    StatsMixin,
    FailedTracksMixin,
    SignedSessionsMixin,
    QueueHistoryMixin,
    NativePlayerMixin,
    LinkMixin,
    PersonalMixin,
    CastMixin,
    ConnectMixin,
    StorageMixin,
    IntegrationsMixin,
):
    """pywebview/`--web` bridge — every method here (plus the two mixins
    above) becomes a callable the frontend invokes as `pywebview.api.<name>`
    (desktop) or `POST /api/<name>` (web mode, see webapp.py's
    ALLOWED_METHODS). Both entry points bind to *this one object*, which is
    why splitting it means moving method bodies into mixins rather than into
    separate top-level objects — see api_mixins/__init__.py for the reasoning
    and api_mixins/local_tagging.py + api_mixins/covers_lyrics.py for what's
    moved out so far. What's still here (search, metadata fetch, the actual
    download orchestration, settings/profiles/registries, window/folder
    plumbing) is intentionally left in place for now rather than split in
    the same pass — those are the highest-traffic, most interdependent
    methods, and this environment can't launch the real pywebview window or
    click through the web GUI to fully exercise a move that size.
    """

    def __init__(self) -> None:
        self._window = None
        # Set for as long as a download is running. Background work that is
        # merely nice to have — the playcount column — waits on this rather
        # than competing with the download for the same API and IP.
        self._download_active = threading.Event()
        # Held for the whole of a download batch, so two of them never share
        # a process — see _await_download_slot().
        self._download_lock = threading.Lock()
        self._active_download_events: set[threading.Event] = set()
        # One per running batch, beside its stop event: set by
        # finish_current_downloads() so the tracks in flight complete and
        # nothing further starts.
        self._active_finish_events: set[threading.Event] = set()
        self._active_download_events_lock = threading.Lock()
        self._stop_generation = 0
        # Set by webapp.py in --web mode: a persisted JobQueue that
        # download_tracks() writes each batch into instead of starting a bare
        # thread, so a restart resumes it (see run_download_job). None
        # everywhere else, the desktop window included — nothing should start
        # downloading by itself because the app was opened.
        self._download_queue = None
        # Tells this process's own download jobs from ones a queue restored
        # from an earlier process — see run_download_job().
        self._session_id = uuid.uuid4().hex
        # A staged-queue item left "running" by a process that died is put
        # back to pending, so Start picks it up again instead of it hanging.
        with contextlib.suppress(Exception):
            from .core import staged_queue

            staged_queue.recover_interrupted()
        # Optional callable set by webapp.py in web mode: fn(event_name, args_list).
        # Desktop (pywebview) mode never sets this and is completely unaffected.
        self._ws_broadcast = None
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        # Which account this instance belongs to, set by webapp.py's
        # ApiRegistry in multi-user mode and left blank everywhere else. It
        # is what the download log is written under, which is what per-account
        # quotas count and what the dashboard reads back.
        self.owner = ""
        self.current_tracks = []
        self.current_url = ""
        # The fetched collection's name when it is a playlist, else "": what
        # a failed track records so its retry goes back to that folder.
        self.current_playlist_name = ""
        # The last tracklists shown on Home, by the id each pushed track
        # carries (list_id): the Queue holds tracks from several of them, and
        # its indices only mean something against the list they came from.
        self._tracklists: OrderedDict[int, tuple[list, str, str]] = OrderedDict()
        self._tracklist_seq = 0
        self.app_version = "unknown"
        # determine application version at construction time so JS can query it early
        try:
            v = importlib.metadata.version("Mornye")
        except Exception:
            v = "unknown"
            try:
                tomllib: Any
                try:
                    import tomllib
                except Exception:
                    tomllib = None
                pyproj = Path(__file__).resolve().parents[1] / "pyproject.toml"
                if pyproj.exists():
                    if tomllib is not None:
                        with pyproj.open("rb") as f:
                            data = tomllib.load(f)
                            v = data.get("project", {}).get("version", v)
                    else:
                        text = pyproj.read_text(encoding="utf-8")
                        m = re.search(r'^version\s*=\s*"([^\"]+)"', text, re.MULTILINE)
                        if m:
                            v = m.group(1)
            except Exception:
                _quiet_log.debug(
                    "Could not read the version from pyproject.toml", exc_info=True
                )
        self.app_version = v

    def set_window(self, window) -> None:
        self._window = window

    def _push(self, fn_name: str, *args) -> None:
        """Sends a frontend event, either as a pywebview evaluate_js call
        (desktop) or as a structured WebSocket message (web mode via
        webapp.py). Exactly one of the two paths runs depending on which
        mode the app was launched in.
        """
        if self._window:
            try:
                js_args = ", ".join(json.dumps(a) for a in args)
                self._window.evaluate_js(f"window.{fn_name}({js_args});")
            except Exception:
                self._push_failed(fn_name, "window")
        if self._ws_broadcast:
            try:
                self._ws_broadcast(fn_name, list(args))
            except Exception:
                self._push_failed(fn_name, "websocket")

    @staticmethod
    def _push_failed(fn_name: str, path: str) -> None:
        """Never raises, and never recurses: logging about a failed push
        must not reach the push again (a re-entrant failure is dropped)."""
        if getattr(_push_guard, "active", False):
            return
        _push_guard.active = True
        try:
            with contextlib.suppress(
                Exception
            ):  # logging itself failed: nothing left to tell
                _quiet_log.debug(
                    "GUI push %s via %s failed", fn_name, path, exc_info=True
                )
        finally:
            _push_guard.active = False

    def _on_loaded(self) -> None:
        """Initializes the frontend after the webview finishes loading.

        Starts extension initialization and updates the frontend with stored history, profiles, and the application version when a window is available.

        Everything logged here is a startup diagnostic, so it goes out as
        "debug": it lands in the Logs view but raises no toast. A stack of
        six toasts on every launch told the user nothing they had asked
        for. Only the ffmpeg/Node *failure* paths below stay at "error",
        because those do need to interrupt.
        """
        self.log("Python Backend connected.", "debug")
        self.log(f"Default download folder: {self.download_dir}", "debug")
        self._check_ffmpeg_startup()
        self._check_node_startup()
        try:
            self.log("Download extension...", "debug")
            self._install_download_extensions()
        except Exception as e:
            self.log(f"Error starting extensions: {e}", "warn")
        try:
            # Scheduled subscriptions (Following view). Once per process, however
            # many times the page loads; see api_mixins/subscriptions.py.
            self._start_subscription_scheduler()
        except Exception as e:
            self.log(f"Could not start the subscription scheduler: {e}", "warn")
        # Mornye Link (api_mixins/link.py): back on if it was left on.
        self._link_autostart()
        app_version = self.app_version
        self._push("loadHistoryAndProfiles")
        self._push("__set_version_label", app_version)

    def get_cli_command(self, url: str, config: dict | None = None) -> dict:
        """The CLI command equivalent to downloading `url` with the GUI's
        current settings (N2): what the TUI's Command panel shows."""
        from .core.cli_preview import gui_command
        from .core.env_settings import gui_env_defaults

        url = str(url or "").strip()
        if not url:
            return {"status": "error", "error": "No link to turn into a command."}
        try:
            # Same layering as a GUI download (_queue_download_batch): the
            # MORNYE_<SETTING> defaults, then what the page sends.
            merged = {**gui_env_defaults(), **dict(config or {})}
            command = gui_command(url, merged, str(self.download_dir))
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "command": command}

    # Expose simple getters to the frontend via pywebview
    def get_version(self):
        return self.app_version

    def get_latest_version(self) -> dict:
        async def _inner():
            try:
                client = AsyncHttpClient("github", timeout_s=10)
                resp = await client.get(
                    "https://api.github.com/repos/mornyeapp/Mornye-Python/releases/latest",
                    headers={
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "Mornye-Desktop",
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    return {"latest_version": "", "published_at": ""}
                data = resp.json() or {}
                return {
                    "latest_version": str(data.get("tag_name", "") or "")
                    .lstrip("v")
                    .strip(),
                    "published_at": str(data.get("published_at", "") or ""),
                }
            except Exception:
                return {"latest_version": "", "published_at": ""}

        return run_sync(_inner())

    #: The project's PyPI name; the version shown in the window is the one
    #: published there (get_pypi_version), so it matches what pip installs.
    PYPI_PROJECT = "mornye"

    def get_pypi_version(self) -> dict:
        """Latest released version on PyPI, or "" when it cannot be reached."""

        async def _inner():
            try:
                client = AsyncHttpClient("pypi", timeout_s=10)
                resp = await client.get(
                    f"https://pypi.org/pypi/{self.PYPI_PROJECT}/json",
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Mornye-Desktop",
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    return {"version": ""}
                info = (resp.json() or {}).get("info") or {}
                return {"version": str(info.get("version", "") or "").strip()}
            except Exception:
                return {"version": ""}

        return run_sync(_inner())

    def _check_ffmpeg_startup(self) -> None:
        try:
            from .core.ffmpeg_check import check_ffmpeg

            result = check_ffmpeg()
            if result["available"]:
                short = result["version"][:80]
                self.log(f"ffmpeg: {short}", "debug")
            else:
                self.log(
                    "⚠  ffmpeg not found — Tidal FLAC muxing and Amazon "
                    "decryption will fail. MP3 transcoding will try to install "
                    "it automatically the first time you use it; otherwise "
                    "install: https://ffmpeg.org/download.html",
                    "error",
                )
                self._push("showFfmpegWarning", result)
        except Exception as exc:
            self.log(f"ffmpeg check error: {exc}", "warn")

    def _check_node_startup(self) -> None:
        # Informational only — this never attempts to install Node itself;
        # that only happens lazily, the first time a JS extension actually
        # runs (see extensions/runtime.py's JSRuntime.start() /
        # core/node_check.py's ensure_node_installed()).
        try:
            from .core.node_check import check_node

            result = check_node()
            if result["available"]:
                self.log(f"Node.js: {result['version']}", "debug")
            else:
                self.log(
                    "⚠  Node.js not found — JavaScript extensions won't work "
                    "until it's installed. Mornye will try to install it "
                    "automatically the first time you use one; see the "
                    "Extensions section of the README for the supported "
                    "package managers, or install it yourself: "
                    "https://nodejs.org/en/download",
                    "error",
                )
                self._push("showNodeWarning", result)
        except Exception as exc:
            self.log(f"Node.js check error: {exc}", "warn")

    def get_artist_images(self, url):
        # Not implemented: returns an empty list to trigger the JS fallback
        return []

    # ── Optional public method (JS can query it later as well) ─────────────
    def get_ffmpeg_status(self) -> dict:
        from .core.ffmpeg_check import check_ffmpeg

        return check_ffmpeg()

    def get_node_status(self) -> dict:
        from .core.node_check import check_node

        return check_node()

    # ── UI communication ──────────────────────────────────────────────────────

    def log(self, message, type="") -> None:
        """Sends one line to the frontend log.

        `type` selects both the colour in the Logs view and whether a toast
        pops: "ok"/"info"/"warn"/"error" toast, "debug" (and the empty
        default) are log-only.

        The bar for a toast is "the user would be worse off for missing
        this", not "something happened". In practice that means outcomes
        and summaries, not narration: "Downloading X…" followed by "X
        saved" is two popups for one event, and a per-item line inside a
        loop is one popup per track. Both belong at "debug" — they are
        still in the Logs view, one click away, and the closing summary
        carries the counts.

        For a list the user does want itemised in the panel — the unmatched
        rows of a CSV import, say — append "-quiet" to the type
        ("error-quiet", "warn-quiet"): the line keeps its colour there and
        raises no toast, leaving one summary toast to stand for the list.
        """
        self._push("app_log", str(message), type)

    def set_progress(self, label="") -> None:
        self._push("app_set_progress", label)

    def set_metadata(
        self,
        title,
        artist,
        cover="",
        quality="FLAC",
        playlist_description=None,
        playlist_followers=None,
        playlist_owner="",
        playlist_owner_avatar="",
        source="",
        artist_listeners=None,
        artist_rank=None,
        artist_verified=False,
        artist_biography=None,
        release_date=None,
        track_count=None,
        artist_url="",
        artists_data=None,
        artist_concerts=None,
        artist_avatar="",
        artist_logo="",
        artist_video="",
        artist_bg_color="",
        artist_text_color="",
    ) -> None:
        payload = {
            "title": title,
            "artist": artist,
            "cover": cover,
            "quality": quality,
        }
        if artist_url:
            payload["artist_url"] = artist_url
        if artists_data:
            payload["artists_data"] = artists_data
        if playlist_description is not None:
            payload["description"] = playlist_description
        if playlist_followers is not None:
            payload["followers"] = playlist_followers
        if playlist_owner:
            payload["owner"] = playlist_owner
        if playlist_owner_avatar:
            payload["owner_avatar"] = playlist_owner_avatar
        if source:
            payload["source"] = source
        if artist_listeners is not None:
            payload["artist_listeners"] = artist_listeners
        if artist_rank is not None:
            payload["artist_rank"] = artist_rank
        if artist_verified:
            payload["artist_verified"] = artist_verified
        if artist_biography:
            payload["artist_biography"] = artist_biography
        if artist_concerts:
            payload["artist_concerts"] = artist_concerts
        if artist_avatar:
            payload["artist_avatar"] = artist_avatar
        # Apple Music's artist header: wordmark PNG, motion video and the
        # colours its preview frame was graded in (hex, no "#").
        if artist_logo:
            payload["artist_logo"] = artist_logo
        if artist_video:
            payload["artist_video"] = artist_video
        if artist_bg_color:
            payload["artist_bg_color"] = artist_bg_color
        if artist_text_color:
            payload["artist_text_color"] = artist_text_color
        if release_date:
            payload["release_date"] = release_date
        if track_count is not None:
            payload["track_count"] = track_count

        self._push("app_set_metadata", payload)

    def _fetch_track_playcounts(
        self,
        sp_client,
        track_ids: list[str],
    ) -> dict[str, dict]:
        """Retrieves playcount per track in parallel using get_track_stats.

        There is no bulk form of this query — an arbitrary list of tracks
        costs one request each — so a large CSV means thousands of them, and
        SpotifyWebClient.query() has no 429 handling of its own (only a 401
        refresh). Two things keep that from turning into a burst against
        Spotify's internal API:

        - `PLAYCOUNT_MIN_INTERVAL_S` spaces request *starts* globally, no
          matter how many workers are running, so throughput is a property
          of this constant rather than of the pool size.
        - a download in progress pauses the whole thing. Playcounts are a
          column in a table; a download is what the user actually asked for,
          and the two share an IP and an origin, so the column waits.
        """
        stats_map: dict[str, dict] = {}
        unique_ids = [tid for tid in dict.fromkeys(track_ids) if tid]
        if not unique_ids:
            return stats_map

        gate = threading.Lock()
        next_start = [0.0]

        def _fetch_one(track_id: str) -> dict:
            # Yield entirely while a download is running.
            while self._download_active.is_set():
                time.sleep(0.5)
            with gate:
                now = time.monotonic()
                wait = next_start[0] - now
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()
                next_start[0] = now + PLAYCOUNT_MIN_INTERVAL_S
            return sp_client.get_track_stats(track_id)

        max_workers = min(PLAYCOUNT_WORKERS, len(unique_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(_fetch_one, track_id): track_id
                for track_id in unique_ids
            }
            for future in as_completed(future_to_id):
                track_id = future_to_id[future]
                try:
                    stats = future.result()
                    if stats.get("playcount"):
                        stats_map[track_id] = stats
                except Exception:
                    continue
        return stats_map

    #: At most this many tracks of one non-Spotify list get a playcount
    #: looked up: two requests each (the ISRC search, then the count), spaced
    #: by PLAYCOUNT_MIN_INTERVAL_S, so a long artist list stays a few seconds.
    ISRC_PLAYCOUNT_MAX = 150

    def _start_isrc_playcounts(self, track_data: list, list_id: int) -> None:
        """Fills the Playcount column of a non-Spotify list (Apple Music and
        the like) after the table is up.

        Those services publish no play counts. Their tracks carry an ISRC,
        though, so each one is found on Spotify by `isrc:` search — an
        identity lookup, never a guess from title and artist — and that
        recording's count is patched into the row (app_update_playcounts,
        keyed by the row's own track id). Rows with no ISRC, or not on
        Spotify, keep "—". Never fatal; a newer list stops the work.
        """
        wanted = [
            (t.get("id"), t.get("isrc"))
            for t in track_data
            if t.get("id") and t.get("isrc") and not t.get("playcount")
        ][: self.ISRC_PLAYCOUNT_MAX]
        if not wanted:
            return

        def _work() -> None:
            try:
                from .core.spotfetch import SpotifyWebClient
                from .core.spotify_metadata import SpotifyMetadataClient

                search = SpotifyMetadataClient()

                async def _find_all() -> dict:
                    found: dict[str, str] = {}
                    gate = asyncio.Semaphore(4)

                    async def _one(row_id: str, isrc: str) -> None:
                        async with gate:
                            if self._tracklist_seq != list_id:
                                return
                            try:
                                hits = await search.search_tracks_async(
                                    f"isrc:{isrc}", limit=1
                                )
                            except Exception:
                                return
                            spotify_id = getattr(hits[0], "id", "") if hits else ""
                            if spotify_id:
                                found[row_id] = spotify_id

                    await asyncio.gather(*(_one(r, i) for r, i in wanted))
                    return found

                by_row = run_sync(_find_all())
                if not by_row or self._tracklist_seq != list_id:
                    return
                client = SpotifyWebClient()
                client.initialize()
                stats = self._fetch_track_playcounts(client, list(by_row.values()))
                if self._tracklist_seq != list_id:
                    return
                counts = {
                    row_id: stats[sid].get("playcount", "")
                    for row_id, sid in by_row.items()
                    if sid in stats
                }
                if counts:
                    self._push_safe("app_update_playcounts", counts)
                    self.log(
                        f"Playcount filled in for {len(counts)} of {len(wanted)} track(s) by ISRC.",
                        "debug",
                    )
            except Exception as e:
                self.log(f"Playcount unavailable: {e}", "debug")

        threading.Thread(target=_work, daemon=True).start()

    # ── Profile & History API ─────────────────────────────────────────────────

    def save_settings(self, cfg: dict) -> None:
        try:
            with _SETTINGS_LOCK:
                write_json_atomic(_settings_file(), cfg)
        except Exception as e:
            self.log(f"Failed to save settings: {e}", "error")

    def _update_settings(self, changes: dict) -> None:
        """Merges `changes` into gui-settings.json as one locked step.

        Merges into the file as it is on disk, not into load_settings()'s
        view of it: that one carries defaults and MORNYE_* environment
        values that are meant to stay out of the file until the user saves
        the Settings form.

        A file that exists but can't be parsed is set aside as
        gui-settings.json.corrupt before being replaced, never silently
        overwritten: the old read-modify-write turned any failed read into
        `{}` and then wrote back only the one changed key, wiping every
        other setting.
        """
        with _SETTINGS_LOCK:
            try:
                cfg = _read_settings_file()
            except (ValueError, OSError) as e:
                path = _settings_file()
                backup = path.with_name(path.name + ".corrupt")
                with contextlib.suppress(OSError):
                    shutil.copy2(path, backup)
                self.log(
                    f"gui-settings.json was unreadable ({e}); kept a copy at {backup}",
                    "error",
                )
                cfg = {}
            cfg.update(changes)
            write_json_atomic(_settings_file(), cfg)

    def save_theme(self, mode: str) -> dict:
        """Persists just the colour mode, without touching anything else.

        The theme picker applies immediately, but the rest of the Settings
        form only reaches disk when the user presses Save. Routing the theme
        through save_settings() would therefore either write half-edited
        settings or lose the theme, which is why it gets its own merge here.

        This is also what makes the choice survive a restart at all: the
        desktop window's localStorage is per-origin and has historically not
        been stable across launches (see run_gui()), so gui-settings.json is
        the copy that can be relied on.
        """
        mode = str(mode or "auto")
        if mode not in ("auto", "light", "dark"):
            return {"ok": False, "error": f"unknown theme mode: {mode}"}
        try:
            self._update_settings({"theme": mode})
            return {"ok": True, "theme": mode}
        except Exception as e:
            self.log(f"Failed to save theme: {e}", "error")
            return {"ok": False, "error": str(e)}

    #: Accents the frontend has a block for (css/accents.css; amber, the
    #: default, is tokens.css itself). Kept here as well so an unknown value
    #: can't be written to disk and then applied at boot as a data-accent
    #: nothing styles.
    ACCENTS = (
        "amber",
        "teal",
        "green",
        "blue",
        "purple",
        "pink",
        "orange",
        "red",
        "cyan",
    )
    #: Matches BRAND_VERSION in frontend/app.js: stored next to the accent so
    #: the one-off teal/green -> amber migration there runs once per install.
    BRAND_VERSION = 3

    def save_accent(self, accent: str) -> dict:
        """Persists the accent colour, same reasoning as save_theme()."""
        accent = str(accent or "amber")
        if accent not in self.ACCENTS:
            return {"ok": False, "error": f"unknown accent: {accent}"}
        try:
            self._update_settings({"accent": accent, "brand": self.BRAND_VERSION})
            return {"ok": True, "accent": accent}
        except Exception as e:
            self.log(f"Failed to save accent: {e}", "error")
            return {"ok": False, "error": str(e)}

    #: Row and gap density of the GUI (N4): frontend/css/layout.css.
    DENSITIES = ("extra-compact", "compact", "comfortable", "large", "extra-large")

    def set_page_zoom(self, factor: float) -> dict:
        """Zoom the whole page natively (the 'large' density).

        WKWebView's pageZoom is the same zoom as Cmd-+ in Safari: layout,
        media queries and getBoundingClientRect all agree, so popovers and
        tooltips stay where they belong — CSS zoom does not promise that in
        WebKit. Cocoa only; elsewhere the frontend falls back to CSS zoom.
        """
        try:
            factor = min(max(float(factor), 0.5), 2.0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid zoom factor"}
        if sys.platform != "darwin" or not self._window:
            return {"ok": False, "error": "native zoom unavailable"}
        try:
            from PyObjCTools import AppHelper
            from webview.platforms.cocoa import BrowserView

            view = BrowserView.instances.get(self._window.uid)
            webview_ = getattr(view, "webview", None)
            if webview_ is None or not hasattr(webview_, "setPageZoom_"):
                return {"ok": False, "error": "native zoom unavailable"}
            AppHelper.callAfter(webview_.setPageZoom_, factor)
            return {"ok": True, "zoom": factor}
        except Exception as e:
            _quiet_log.debug("Native page zoom failed", exc_info=True)
            return {"ok": False, "error": str(e)}

    def save_density(self, density: str) -> dict:
        """Persists the density, same reasoning as save_theme()."""
        density = str(density or "comfortable")
        if density not in self.DENSITIES:
            return {"ok": False, "error": f"unknown density: {density}"}
        try:
            self._update_settings({"density": density})
            return {"ok": True, "density": density}
        except Exception as e:
            self.log(f"Failed to save density: {e}", "error")
            return {"ok": False, "error": str(e)}

    #: The look of the GUI: "console" is the monospace console look,
    #: "music" the music-app look (frontend/css/music-tokens.css and
    #: music.css, keyed off data-style="music" on <html> and <body>).
    STYLES = ("console", "music")

    def save_style(self, style: str) -> dict:
        """Persists the interface style, same reasoning as save_theme()."""
        style = str(style or "console")
        if style not in self.STYLES:
            return {"ok": False, "error": f"unknown style: {style}"}
        try:
            self._update_settings({"style": style})
            return {"ok": True, "style": style}
        except Exception as e:
            self.log(f"Failed to save style: {e}", "error")
            return {"ok": False, "error": str(e)}

    def load_settings(self) -> dict:
        try:
            with _SETTINGS_LOCK:
                settings = _read_settings_file()
            if isinstance(settings, dict):
                from .core.animated_artwork import (
                    artwork_api_url,
                    animated_artworks_enabled_by_default,
                )

                settings.setdefault(
                    "save_animated_artwork",
                    animated_artworks_enabled_by_default(),
                )
                settings.setdefault("animated_artwork_api_url", artwork_api_url())
                settings.setdefault("sound_enabled", True)
                settings.setdefault("notification_sound", True)
                # MORNYE_<SETTING> environment variables (core/env_settings):
                # the page shows them until something is saved from it.
                from .core.env_settings import gui_env_defaults

                for key, value in gui_env_defaults().items():
                    settings.setdefault(key, value)
                return settings
        except Exception as e:
            self.log(f"Failed to load settings: {e}", "error")
        return {}

    def list_network_destinations(self) -> list[dict]:
        from .core.network_destinations import DESTINATIONS

        return DESTINATIONS.list()

    def save_network_destination(
        self, profile: dict, password: str | None = None
    ) -> dict:
        from .core.network_destinations import DESTINATIONS, DestinationError

        try:
            saved = DESTINATIONS.save(profile, password, owner=self.owner)
            return {
                "ok": True,
                "destination": saved.public_dict(),
                "credential_storage": DESTINATIONS.credential_storage(
                    saved.id, owner=self.owner
                ),
            }
        except DestinationError as exc:
            return {"ok": False, "error": str(exc)}

    def remove_network_destination(self, profile_id: str) -> dict:
        from .core.network_destinations import DESTINATIONS

        return {"ok": DESTINATIONS.remove(str(profile_id))}

    def test_network_destination(
        self, profile_id: str, password: str | None = None
    ) -> dict:
        from .core.network_destinations import (
            DESTINATIONS,
            DestinationError,
            create_backend,
        )

        profile = DESTINATIONS.get(str(profile_id))
        if profile is None:
            return {"ok": False, "error": "That network destination no longer exists."}
        try:
            secret = DESTINATIONS.credentials(profile, password, owner=self.owner)
            return create_backend(profile, profile.username, secret).test_connection()
        except DestinationError as exc:
            return {"ok": False, "error": str(exc)}

    def retry_network_delivery(self, profile_id: str, staging_id: str) -> dict:
        from .core.network_destinations import (
            DESTINATIONS,
            DestinationError,
            retained_staging_directory,
            create_backend,
        )

        profile = DESTINATIONS.get(str(profile_id))
        if profile is None:
            return {"ok": False, "error": "That network destination no longer exists."}
        try:
            stage = retained_staging_directory(self.owner, str(staging_id))
            password = DESTINATIONS.credentials(profile, owner=self.owner)
            create_backend(profile, profile.username, password).upload_tree(stage)
            shutil.rmtree(stage)
            return {"ok": True, "message": "Retained files delivered and verified."}
        except DestinationError as exc:
            return {"ok": False, "error": str(exc)}

    # ── Extension Registry API ─────────────────────────────────────────────

    def _install_download_extensions(self) -> None:
        """Install the registries' download extensions in the background,
        then tell the page so it re-reads the provider list.

        Before, this ran only at startup and said nothing when it finished:
        a first launch, or a registry added later, left Settings with an
        empty provider list until the next restart.
        """

        def run() -> None:
            try:
                from .extensions.manager import ExtensionManager

                ExtensionManager(auto_install_downloads=True)
            except Exception as e:
                self.log(f"Could not install download extensions: {e}", "warn")
            with contextlib.suppress(Exception):
                self._push("app_extensions_changed")

        threading.Thread(target=run, daemon=True).start()

    def get_registries(self) -> list | dict:
        """Returns every known extension-registry URL with its origin
        (environment variable, .env file, or added from the GUI) and
        whether it is currently enabled."""
        try:
            from .extensions import registry_config

            return registry_config.list_registries()
        except Exception as e:
            self.log(f"Failed to load registries: {e}", "error")
            return {"error": True, "message": str(e)}

    def add_registry(self, url: str) -> dict:
        try:
            from .extensions import registry_config

            registries = registry_config.add_registry(url)
            self.log(f"Registry added: {url}", "debug")
            # A registry is only a list until its extensions are installed:
            # install the download ones now, the same way startup does, so the
            # provider list fills without a restart.
            self._install_download_extensions()
            return {"ok": True, "registries": registries}
        except Exception as e:
            self.log(f"Failed to add registry: {e}", "error")
            return {"ok": False, "error": str(e)}

    def remove_registry(self, url: str) -> dict:
        try:
            from .extensions import registry_config

            registries = registry_config.remove_registry(url)
            self.log(f"Registry removed: {url}", "debug")
            return {"ok": True, "registries": registries}
        except Exception as e:
            self.log(f"Failed to remove registry: {e}", "error")
            return {"ok": False, "error": str(e)}

    def get_history(self):
        try:
            from .core.session_memory import get_url_history_async

            return run_sync(get_url_history_async())
        except Exception:
            return []

    def get_profiles(self):
        try:
            from .core.profiles import list_profiles_async

            return run_sync(list_profiles_async())
        except Exception:
            return []

    def load_profile_data(self, name):
        try:
            from .core.profiles import get_profile_async

            return run_sync(get_profile_async(name)) or {}
        except Exception:
            return {}

    def cache_image(self, url):
        return url

    def get_spotify_home_feed(self):
        """Metodo chiamato da app.js per retrievesre l'Home Feed."""
        try:
            from .core.spotfetch import SpotifyWebClient
            from .core.spotify_metadata import parse_home_feed

            client = SpotifyWebClient()
            raw_data = client.get_home_feed()
            return parse_home_feed(raw_data)
        except Exception as e:
            import logging

            logging.exception(f"Error retrieving Home Feed: {e}")
            return {"success": False, "error": str(e)}

    def search_code(self, query, path=".", limit=200):
        """Search the Mornye package source (substring, case-insensitive).

        Returns list of {path, line, snippet}.

        `path` is confined to the installed package directory. This returns
        matching *lines*, not just filenames, so an unconstrained path would
        make it a general "read any file the process can open" primitive —
        and every public method here is callable from whatever JS is running
        in the window (pywebview binds the whole object as js_api).
        """
        try:
            from .core.code_search import search_code

            root = Path(__file__).resolve().parent
            requested = (root / (path or ".")).resolve()
            if requested != root and root not in requested.parents:
                self.log(
                    f"search_code: '{path}' is outside the package directory",
                    "error",
                )
                return []

            return search_code(query, path=str(requested), limit=limit or 200)
        except Exception as e:
            self.log(f"search_code error: {e}", "error")
            return []

    def remove_history_item(self, url) -> None:
        try:
            from .core.session_memory import remove_url_from_history_async

            run_sync(remove_url_from_history_async(url))
        except Exception as exc:
            self.log(f"Could not remove the link from the history: {exc}", "warn")

    def save_profile_data(self, name, cfg) -> bool | None:
        try:
            from .core.profiles import save_profile_async

            run_sync(save_profile_async(name, cfg))
            self.log(f"Profile '{name}' saved successfully.", "ok")
            return True
        except Exception as e:
            self.log(f"Failed to save profile: {e}", "error")
            return False

    def delete_profile_data(self, name):
        try:
            from .core.profiles import delete_profile_async

            deleted = run_sync(delete_profile_async(name))
            self.log(
                f"Profile '{name}' deleted: {deleted}.",
                "ok" if deleted else "warn",
            )
            return deleted
        except Exception as e:
            self.log(f"Failed to delete profile: {e}", "error")
            return False

    # The Qobuz/Tidal custom-API checks are gone with the options they
    # checked: the provider APIs are now each JS extension's own settings
    # (see api_mixins/extension_settings.py).

    def check_animated_artwork_api(self, url):
        return self._check_api_endpoint_sync(url)

    def _check_api_endpoint_sync(self, url):
        async def _inner():
            try:
                if not url or not isinstance(url, str) or not url.strip():
                    msg = "URL must be a non-empty string"
                    raise ValueError(msg)
                normalized = url.strip()
                if not normalized.lower().startswith("http"):
                    msg = "URL must start with http or https"
                    raise ValueError(msg)
                client = AsyncHttpClient("api-check", timeout_s=10)
                resp = await client.get(normalized, follow_redirects=True, timeout=10.0)
                return {
                    "ok": 200 <= resp.status_code < 400,
                    "status_code": resp.status_code,
                    "url": str(resp.url),
                }
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return run_sync(_inner())

    # ── Window controls ───────────────────────────────────────────────────────

    def WindowMinimise(self) -> None:
        if self._window:
            self._window.minimize()

    def _toggle_zoom_macos(self) -> bool:
        """Toggle the native macOS zoom on the frameless window.

        pywebview's Cocoa backend implements maximize() as a resize to the
        screen size anchored at the window's current origin (so it overflows
        the screen) and restore() as deminiaturize, which does nothing for a
        window that isn't minimised: the green dot could grow the window but
        never bring it back. NSWindow.zoom: fills the visible frame (menu bar
        and Dock excluded) and toggles back to the previous frame.
        """
        native = getattr(self._window, "native", None)
        if native is None:
            return False
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(native.zoom_, None)
        except Exception:
            _quiet_log.debug("Native macOS zoom failed", exc_info=True)
            return False
        self._is_maximized = not getattr(self, "_is_maximized", False)
        return True

    def WindowToggleMaximise(self) -> None:
        if not self._window:
            return
        if sys.platform == "darwin" and self._toggle_zoom_macos():
            return
        if getattr(self, "_is_maximized", False):
            self._window.restore()
            self._is_maximized = False
        elif sys.platform == "win32":
            try:
                import ctypes

                # Get monitor work area (excluding taskbar)
                work_area = ctypes.wintypes.RECT()
                # SPI_GETWORKAREA = 48
                result = ctypes.windll.user32.SystemParametersInfoW(
                    48,
                    0,
                    ctypes.byref(work_area),
                    0,
                )
                if result:
                    width = work_area.right - work_area.left
                    height = work_area.bottom - work_area.top
                    # Use SetWindowPos API for more reliable positioning
                    hwnd = ctypes.windll.user32.FindWindowW(
                        None,
                        self._window.title,
                    )
                    if hwnd:
                        # SWP_NOZORDER = 4, SWP_FRAMECHANGED = 0x20
                        ctypes.windll.user32.SetWindowPos(
                            hwnd,
                            None,
                            work_area.left,
                            work_area.top,
                            width,
                            height,
                            4 | 0x20,
                        )
                    else:
                        self._window.move(work_area.left, work_area.top)
                        self._window.resize(width, height)
                    self._is_maximized = True
                else:
                    # Fallback - use maximize but with adjustment
                    self._window.maximize()
                    self._is_maximized = True
            except Exception as e:
                self.log(f"Maximize error: {e}", "warn")
                try:
                    self._window.maximize()
                    self._is_maximized = True
                except Exception:
                    _quiet_log.debug("Window maximize fallback failed", exc_info=True)
        else:
            self._window.maximize()
            self._is_maximized = True

    def Quit(self) -> None:
        if self._window:
            with contextlib.suppress(Exception):
                self._window.destroy()
        os._exit(0)

    def choose_folder(self) -> None:
        """Desktop-only: opens the native OS folder picker. In web mode
        self._window is always None here, so this is a safe no-op — the web
        frontend uses set_download_dir() (below) with its own server-side
        folder browser instead, since a browser cannot open a native dialog
        that returns a real filesystem path on the server.
        """
        if self._window and folder_dialog_is_builtin():
            # Opened in the page, the same picker the web build uses, which
            # ends in set_download_dir(). See folder_dialog_is_builtin().
            self._push("openDownloadFolderBrowser")
            return
        if self._window:
            dialog_type = getattr(webview, "FileDialog", None)
            if dialog_type is None:
                dialog_type = getattr(webview, "FOLDER_DIALOG", None)
            if dialog_type is None:
                return
            result = self._window.create_file_dialog(
                dialog_type.FOLDER if hasattr(dialog_type, "FOLDER") else dialog_type
            )
            if result and len(result) > 0:
                self.download_dir = result[0]
                self.log(f"Download folder changed: {self.download_dir}", "ok")
                with contextlib.suppress(Exception):
                    self._push("updateFolderLabel", self.download_dir)

    def get_home_dir(self) -> str:
        """Return the user's home dir for desktop and browser folder pickers."""
        return str(Path.home())

    def get_network_shares(self) -> dict:
        """Network shares this machine has mounted, for the library pickers."""
        from .core.network_paths import describe_shares

        try:
            return {"ok": True, "shares": describe_shares()}
        except Exception as e:
            return {"ok": False, "shares": [], "error": str(e)}

    def browse_folder(self, path: str | None = None) -> dict:
        """List subdirectories and files for a folder, matching the web-mode /api/browse-folder payload."""
        if path:
            from .core.network_paths import resolve_library_path

            mapped, _error = resolve_library_path(path)
            path = mapped or path
        base = Path(path).expanduser() if path else Path.home()
        try:
            base = base.resolve()
            if not base.is_dir():
                base = Path.home().resolve()
            directories = sorted(
                (
                    p.name
                    for p in base.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                ),
                key=str.lower,
            )
            files = sorted(
                (
                    p.name
                    for p in base.iterdir()
                    if p.is_file() and not p.name.startswith(".")
                ),
                key=str.lower,
            )
        except Exception as e:
            return {
                "error": str(e),
                "path": str(base),
                "parent": None,
                "directories": [],
                "files": [],
            }
        parent = str(base.parent) if base.parent != base else None
        return {
            "path": str(base),
            "parent": parent,
            "directories": directories,
            "files": files,
        }

    def _notify_target_for_run(self):
        """(target, report) for this run's environment-configured notifier.

        `report` is a RunReport to install as a hook when the notifier wants
        one message per run ("summary"), else None. (None, None) when
        MORNYE_NOTIFY is unset; a misconfigured one is logged, not raised —
        a broken notifier must not cost the download.
        """
        try:
            from .core.notifiers import target_from_env

            target = target_from_env()
        except Exception as exc:
            self.log(f"Notifications are misconfigured: {exc}", "warn")
            return None, None
        if target is None:
            return None, None
        if target.on == "summary":
            from .core.report import RunReport

            return target, RunReport()
        return target, None

    def get_download_dir(self) -> dict:
        """The folder downloads go to right now.

        The page used to show a "~/Music/Mornye" written into its HTML until
        the folder was changed, so a container started with
        MORNYE_DOWNLOAD_DIR (or a per-account folder in multi-user mode)
        displayed a path nothing was saved to (issue #254). The page asks
        for the real one at start-up.
        """
        return {"download_dir": self.download_dir}

    def set_download_dir(self, path: str) -> dict:
        """Web-mode equivalent of choose_folder(): sets the download
        directory to a path chosen via the server-side folder browser
        (see webapp.py's /api/browse-folder). Validates that the path
        exists and is a directory on the server before accepting it.
        """
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"Not a directory: {path}"}
        self.download_dir = str(p)
        self.log(f"Download folder changed: {self.download_dir}", "ok")
        with contextlib.suppress(Exception):
            self._push("updateFolderLabel", self.download_dir)
        return {"ok": True, "download_dir": self.download_dir}

    def open_config_folder(self) -> None:
        config_dir = str(cache_dir())
        try:
            os.makedirs(config_dir, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", config_dir])
            elif sys.platform == "win32":
                os.startfile(config_dir)
            else:
                subprocess.Popen(["xdg-open", config_dir])
            self.log(f"Opened config folder: {config_dir}", "ok")
        except Exception as e:
            self.log(f"Failed opening config folder: {e}", "error")

    def reveal_in_folder(self, path) -> dict:
        """Shows a music file selected in the system file manager (Finder,
        Explorer; on Linux its folder opens).

        Desktop window only: it is left out of the web interface's allowed
        methods (webapp.py), where it would open a window on the server.
        Only an existing audio file is accepted, and it is shown, never
        opened, so nothing the path names can run.
        """
        from .core.playlist_sync import AUDIO_EXTENSIONS

        try:
            p = Path(str(path or "")).expanduser()
            if not p.is_absolute() or not p.is_file():
                return {
                    "ok": False,
                    "error": "That file is not on this computer any more.",
                }
            if p.suffix.lower() not in AUDIO_EXTENSIONS:
                return {"ok": False, "error": "Only music files can be shown."}
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{p}"])
            else:
                subprocess.Popen(["xdg-open", str(p.parent)])
            return {"ok": True}
        except Exception as e:
            self.log(f"Failed showing {path}: {e}", "error")
            return {"ok": False, "error": str(e)}

    def open_url(self, url) -> None:
        """Opens an http(s) URL in the user's browser.

        The scheme check is not cosmetic: webbrowser.open() delegates to
        `open` on macOS and `xdg-open` on Linux, both of which resolve every
        scheme the desktop has a handler registered for — file://, and
        whatever custom scheme any installed application claimed. Passing
        those through would turn "open a link" into "launch an arbitrary
        local handler", reachable from any JS running in the window.
        """
        import webbrowser
        from urllib.parse import urlparse

        try:
            scheme = urlparse(str(url)).scheme.lower()
        except Exception:
            scheme = ""
        if scheme not in ("http", "https"):
            self.log(
                f"Refused to open a non-web URL (scheme: {scheme or 'none'})", "error"
            )
            return

        webbrowser.open(url)

    # ── Lazy Loading - Track preview ──────────────────────────────────────

    def get_track_preview(self, track_id: str) -> str:
        """Retrieves the preview URL for a track (lazy loading).

        This method is only invoked by the GUI when the user clicks 'play' or 'preview'
        to avoid network requests during initial list loading.

        Args:
            track_id: Spotify track ID

        Returns:
            MP3 preview URL (empty string if unavailable)

        """
        try:
            from .core.spotify_metadata import SpotifyMetadataClient

            client = SpotifyMetadataClient()
            preview_url = run_sync(client.get_track_preview_async(track_id))
            return preview_url or ""
        except Exception as e:
            self.log(f"Failed to fetch preview for track {track_id}: {e}", "debug")
            return ""

    # ── Phase 1: Metadata fetch ───────────────────────────────────────────────

    def fetch_metadata(self, url) -> None:
        self.current_url = url
        threading.Thread(
            target=lambda: run_sync(self._fetch_metadata_task(url)),
            daemon=True,
        ).start()

    def _spotify_playcounts(self, url: str) -> dict:
        """Play counts for a Spotify link, by track id; {} when unavailable.

        Runs in a worker thread, alongside the metadata fetch.
        """
        try:
            from .core.spotfetch import SpotifyWebClient

            sp_client = SpotifyWebClient()
            sp_client.initialize()
            lower_url = url.lower()
            if "/artist/" in lower_url or "spotify:artist:" in lower_url:
                return {}
            if playlist_match := re.search(r"playlist[:/]([a-zA-Z0-9]+)", url):
                self.log("Attempting to fetch playcount…", "debug")
                return sp_client.get_playlist_stats(playlist_match.group(1)) or {}
            if album_match := re.search(r"album[:/]([A-Za-z0-9]+)", url):
                self.log(
                    "Attempting to fetch playcount for album (fast mode)…", "debug"
                )
                return sp_client.get_album_stats(album_match.group(1)) or {}
            if track_match := re.search(r"track[:/]([a-zA-Z0-9]+)", url):
                self.log("Attempting to fetch playcount…", "debug")
                track_id = track_match.group(1)
                stats = sp_client.get_track_stats(track_id)
                return {track_id: stats["playcount"]} if stats.get("playcount") else {}
        except Exception as exc:
            self.log(f"Playcount unavailable: {type(exc).__name__}", "debug")
        return {}

    async def _fetch_metadata_task(self, url) -> None:
        try:
            self.set_progress("Retrieving metadata…")
            self.log(f"Analyzing input: {url}", "debug")

            # ── Detection: is it a URL or a search query? ──────────────────
            stripped = url.strip()
            is_url = stripped.startswith(("http", "spotify:"))

            if not is_url:
                self.log("Text search: use search_provider_async.", "error")
                self.set_progress("")
                return

            # Domain → client. Shared with the terminal UI's track picker
            # via core/tracklist.py: a provider added to one mapping and not
            # the other is a link that works in one window and not the other.
            #
            # After the guard, not before it: a text query resolved to a
            # client it was never going to use, which for the Spotify default
            # means constructing SpotifyMetadataClient — and its credential
            # setup — for every search typed into the box.
            from .core.tracklist import metadata_client_for

            # ── Universal call ──────────────────────────────────────────────────
            # get_url() is sync on SpotifyMetadataClient but async on
            # TidalMetadataClient/AppleMusicMetadataClient — calling it directly
            # here would hand back an un-awaited coroutine for the latter two
            # (crashing on the very next line, `result[0]`). Reuse the same
            # sync/async dispatch helper the real download path already relies
            # on instead of duplicating (and re-diverging from) that logic —
            # through the fallback that asks an installed metadata extension
            # when the built-in Spotify or Apple Music client fails.
            # Returns (name, tracks) OR (name, tracks, cover) OR (name, tracks, cover, meta).
            from .core.metadata_fallback import get_url_with_fallback

            # Spotify play counts need only the link, so they are fetched
            # while the metadata loads instead of after it: a separate
            # client with its own session, close to a second of waiting
            # that used to come on top of the metadata.
            playcount_task = (
                asyncio.ensure_future(asyncio.to_thread(self._spotify_playcounts, url))
                if url_host_matches(url, "spotify.com")
                else None
            )
            try:
                result = await get_url_with_fallback(
                    stripped, lambda: metadata_client_for(url)
                )
            except BaseException:
                if playcount_task is not None:
                    playcount_task.cancel()
                raise
            collection_name = result[0]
            tracks = result[1]
            collection_cover = result[2] if len(result) > 2 else ""
            collection_meta = result[3] if len(result) > 3 else {}

            cover = collection_cover or ""
            lower_url = url.lower()
            is_playlist = ("/playlist/" in lower_url) or (
                "list=" in lower_url and "olak5uy_" not in lower_url
            )
            is_artist = "/artist/" in lower_url or "spotify:artist:" in lower_url

            if not cover and not is_playlist and not is_artist and tracks:
                cover = getattr(tracks[0], "cover_url", "") or ""

            if not tracks:
                if playcount_task is not None:
                    playcount_task.cancel()
                self.log("No tracks found at this URL.", "error")
                return

            self.current_tracks = tracks
            self.current_playlist_name = collection_name if is_playlist else ""
            list_id = self._remember_tracklist(tracks, url, self.current_playlist_name)
            track_data = []

            playcount_map: dict = {}
            if playcount_task is not None:
                try:
                    playcount_map = await playcount_task or {}
                except Exception:
                    _quiet_log.debug("Playcount lookup failed", exc_info=True)
            # A track link's count is kept only when the link did give one
            # track (an artist link can contain "/track/" further along).
            if (
                len(tracks) != 1
                and re.search(r"track[:/]", url)
                and not re.search(r"(playlist|album)[:/]", url)
            ):
                playcount_map = {}

            # Apple Music ranks an artist's Top Songs itself (no play counts):
            # their order, keyed by ISRC and by song id, ranks the rows.
            top_rank: dict[str, int] = {}
            for rank, song in enumerate(
                collection_meta.get("top_songs") or [], start=1
            ):
                for key in (
                    song.get("isrc"),
                    song.get("id"),
                    f"apple_{song.get('id')}",
                ):
                    if key and key not in top_rank:
                        top_rank[str(key)] = rank

            # ── Enhanced metadata-extraction loop ──
            for i, t in enumerate(tracks):
                track_id = getattr(t, "id", "")

                # Try to retrieve data dynamically
                title = getattr(t, "title", getattr(t, "name", f"Track {i + 1}"))
                # Handles both cases where 'artists' is a string or a list
                raw_art = getattr(t, "artists", getattr(t, "artist", "Unknown"))
                artist = (
                    ", ".join(raw_art) if isinstance(raw_art, list) else str(raw_art)
                )

                # Take the album if it exists
                album = getattr(t, "album", getattr(t, "album_name", "—"))

                _pc_val = playcount_map.get(track_id, "") if playcount_map else ""
                playcount = (
                    _pc_val.get("playcount", "")
                    if isinstance(_pc_val, dict)
                    else _pc_val
                )
                if not playcount or playcount == "0":
                    fallback_plays = getattr(t, "plays", "")
                    playcount = (
                        fallback_plays
                        if fallback_plays and fallback_plays != "0"
                        else ""
                    )

                track_data.append(
                    {
                        "index": i,
                        "list_id": list_id,
                        "id": track_id,
                        "title": title,
                        "artist": artist,
                        "artist_url": getattr(t, "artist_url", ""),
                        # One {id,name,url} per credited artist, so a row can
                        # link each of them rather than the joined string.
                        "artists_data": getattr(t, "artists_data", []) or [],
                        "album": album,
                        # Lets the artist view's album cards open the album
                        # they stand for instead of only queueing it.
                        "album_url": getattr(t, "album_url", ""),
                        "cover": getattr(t, "cover_url", ""),
                        "duration_ms": getattr(t, "duration_ms", 0),
                        "explicit": getattr(t, "is_explicit", False),
                        "isrc": getattr(t, "isrc", ""),
                        "external_url": getattr(t, "external_url", ""),
                        "preview_url": getattr(t, "preview_url", ""),
                        "playcount": playcount,
                        # Apple Music: the album's popularity of the track
                        # (0-1) and its place among the artist's Top Songs.
                        "popularity": (getattr(t, "extra_info", None) or {}).get(
                            "apple_popularity"
                        ),
                        "top_rank": top_rank.get(str(getattr(t, "isrc", "") or ""))
                        or top_rank.get(str(track_id)),
                        "credits": (getattr(t, "extra_info", None) or {}).get(
                            "apple_credits"
                        )
                        or [],
                        # Lets an artist page sort its releases into albums,
                        # singles & EPs, and other artists' releases.
                        "album_type": getattr(t, "album_type", "") or "",
                        "album_artist": getattr(t, "album_artist", "") or "",
                        "release_date": getattr(t, "release_date", ""),
                        "copyright": getattr(t, "copyright", ""),
                        # For the album card's technical sheet. Every provider
                        # fills these in unevenly, so the frontend drops any
                        # row that comes back empty rather than showing a gap.
                        "publisher": getattr(t, "publisher", ""),
                        "upc": getattr(t, "upc", ""),
                        "genre": getattr(t, "genre", ""),
                    },
                )

            badge = f"FLAC — {len(tracks)} tracks" if len(tracks) > 1 else "FLAC"

            # For artist URLs show only artist name
            lower_url = url.lower()
            is_artist = "/artist/" in lower_url or "spotify:artist:" in lower_url
            if is_artist:
                display_title = collection_name
                display_artist = ""
                display_artist_url = ""
                display_artists_data: list = []
            elif is_playlist:
                # A playlist is by its curator, not by whoever sings its first
                # track: that put "Justice, Tame Impala, SZA, …" under "Top
                # 100: Global", as one link that led nowhere.
                display_title = collection_name
                display_artist = collection_meta.get("owner", "") or ""
                display_artist_url = ""
                display_artists_data = []
            else:
                display_title = collection_name
                display_artist = tracks[0].artists if tracks else ""
                display_artist_url = tracks[0].artist_url if tracks else ""
                display_artists_data = (tracks[0].artists_data if tracks else []) or []
                # An album is by its album artist, not by everyone on its
                # first track: "Babyface Ray, Babyface" headed Babyface Ray's
                # album because track 1 has a guest. A track link keeps the
                # track's own credits.
                is_track_link = len(tracks) == 1 and bool(
                    re.search(r"(track|song)[:/]|/album/[^?]*[?&]i=\d+", lower_url)
                )
                album_artist = (
                    str(getattr(tracks[0], "album_artist", "") or "") if tracks else ""
                )
                owner_names = (
                    list(getattr(tracks[0], "album_artist_names", None) or [])
                    if tracks
                    else []
                )
                if album_artist and owner_names and not is_track_link:
                    owners = {n.lower() for n in owner_names}
                    display_artist = album_artist
                    display_artists_data = [
                        a
                        for a in display_artists_data
                        if str(a.get("name", "")).lower() in owners
                    ]

            if is_artist:
                # The hero is a wide banner, so it takes the artist's header
                # image like Spotify's own page does; the square avatar stays
                # the cover everywhere else (the fetch history thumbnail).
                self.set_metadata(
                    display_title,
                    display_artist,
                    collection_meta.get("header") or cover,
                    badge,
                    artist_listeners=collection_meta.get("listeners"),
                    artist_rank=collection_meta.get("rank"),
                    artist_verified=collection_meta.get("verified", False),
                    artist_biography=collection_meta.get("biography", ""),
                    artist_concerts=collection_meta.get("concerts"),
                    artist_avatar=collection_meta.get("avatar") or cover,
                    artist_logo=collection_meta.get("logo", ""),
                    artist_video=collection_meta.get("header_video", ""),
                    artist_bg_color=collection_meta.get("bg_color", ""),
                    artist_text_color=collection_meta.get("text_color", ""),
                )
            else:
                self.set_metadata(
                    display_title,
                    display_artist,
                    cover,
                    badge,
                    playlist_description=collection_meta.get("description"),
                    playlist_followers=collection_meta.get("followers"),
                    playlist_owner=collection_meta.get("owner", ""),
                    playlist_owner_avatar=collection_meta.get("owner_avatar", ""),
                    source=collection_meta.get("source", ""),
                    release_date=collection_meta.get("release_date"),
                    track_count=collection_meta.get("track_count"),
                    artist_url=display_artist_url,
                    artists_data=display_artists_data,
                )

            self.log(
                f"Found: {collection_name} ({len(tracks)} track(s)). Choose songs to download.",
                "ok",
            )
            self.set_progress("Ready for download.")

            try:
                from urllib.parse import urlparse

                from .core.session_memory import add_url_to_history_async

                _lower = url.lower()
                # By host, not substring: "music.apple.com" may sit anywhere
                # in a URL that is not Apple's.
                _host = (urlparse(_lower).hostname or "") if "://" in _lower else ""
                _apple = _host == "music.apple.com" or _host.endswith(
                    ".music.apple.com"
                )
                if (
                    "/track/" in _lower
                    or _lower.startswith("spotify:track:")
                    or "watch?v=" in _lower
                    or "youtu.be" in _lower
                    or (_apple and "/song/" in _lower)
                    or ("/album/" in _lower and re.search(r"[?&]i=\d+", _lower))
                ):
                    _url_type = "track"
                elif "/album/" in _lower or _lower.startswith("spotify:album:"):
                    _url_type = "album"
                elif (
                    "/playlist/" in _lower
                    or _lower.startswith("spotify:playlist:")
                    or ("list=" in _lower and "olak5uy_" not in _lower)
                ):
                    _url_type = "playlist"
                elif "/artist/" in _lower or _lower.startswith("spotify:artist:"):
                    _url_type = "artist"
                else:
                    _url_type = ""

                _artist = (
                    getattr(tracks[0], "artists", "")
                    if tracks and _url_type == "track"
                    else ""
                )
                await add_url_to_history_async(
                    url,
                    label=collection_name,
                    cover=cover,
                    track_count=len(tracks),
                    url_type=_url_type,
                    artist=_artist,
                )
            except Exception:
                _quiet_log.debug(
                    "Could not add %s to the fetch history", url, exc_info=True
                )

            self._push("showTracklist", track_data)
            # Apple Music and the other services publish no play counts:
            # look them up on Spotify by ISRC, behind the table.
            if not url_host_matches(url, "spotify.com"):
                self._start_isrc_playcounts(track_data, list_id)

        except Exception as e:
            self.log(f"Error fetching metadata: {e!s}", "error")
            self.set_progress("Error.")

    # ── Phase 2: Download ─────────────────────────────────────────────────────

    def _remember_tracklist(self, tracks: list, url: str, playlist_name: str) -> int:
        """Keeps a tracklist shown on Home; its id rides on every pushed track."""
        self._tracklist_seq += 1
        self._tracklists[self._tracklist_seq] = (list(tracks), url or "", playlist_name)
        while len(self._tracklists) > 16:
            self._tracklists.popitem(last=False)
        return self._tracklist_seq

    def download_tracks(self, selected_indices, config, list_id=None) -> None:
        """Downloads tracks picked out of a tracklist shown on Home.

        `list_id` names that tracklist (every track the page got carries it).
        The Queue can hold tracks from several lists by the time it starts,
        and positions read against whatever was fetched last downloaded the
        wrong tracks into the wrong playlist (issue #277). Without it the
        current list is meant, as before.
        """
        context = None
        if list_id is not None:
            try:
                context = self._tracklists.get(int(list_id))
            except (TypeError, ValueError):
                context = None
            if context is None:
                self.log(
                    "That list is no longer loaded: open it again and re-queue "
                    "its tracks.",
                    "error",
                )
                self._push_safe("app_download_finished", False)
                return
        payload = self._download_job_payload(selected_indices, config, context)
        if payload is not None:
            if self._download_queue is not None:
                self._submit_download_job(payload)
            else:
                # Self-contained in the desktop window too: the batch may wait
                # for the one before it, and by then Home can show another list.
                payload["_stop_generation"] = self._current_stop_generation()
                self._start_download_job(payload)
            return
        if context is not None and context[0] is not self.current_tracks:
            self.log("Could not queue these tracks.", "error")
            self._push_safe("app_download_finished", False)
            return
        threading.Thread(
            target=self._download_task,
            args=(selected_indices, config),
            kwargs={"expected_generation": self._current_stop_generation()},
            daemon=True,
        ).start()

    def save_playlist_m3u(self, config=None, list_id=None) -> dict:
        """Writes the M3U of a playlist shown on Home, downloading nothing.

        For a playlist whose M3U was deleted, or never written: its tracks are
        looked up in the download folder (every playlist folder in it) and
        in the "existing library folders", and the file goes where a download
        of the playlist puts it — ``<folder>/<Playlist>/<Playlist>.m3u8`` —
        listing the tracks found, in the playlist's order.
        """
        from .core.models import TrackMetadata

        config = dict(config or {})
        context = None
        if list_id is not None:
            try:
                context = self._tracklists.get(int(list_id))
            except (TypeError, ValueError):
                context = None
        if context is None:
            context = (
                self.current_tracks,
                self.current_url or "",
                getattr(self, "current_playlist_name", ""),
            )
        listed, _url, playlist_name = context
        tracks = [t for t in listed if isinstance(t, TrackMetadata)]
        if not (playlist_name or "").strip() or not tracks:
            return {"ok": False, "error": "Open a playlist first."}
        return self._write_playlist_m3u(config, playlist_name, tracks)

    def _write_playlist_m3u(
        self, config: dict, playlist_name: str, tracks: list, *, quiet: bool = False
    ) -> dict:
        """Rewrites a playlist's M3U to `tracks`, from the files on disk.

        Shared by "Save .m3u8" and the followed-playlist check, which calls
        it with the playlist's full current listing so a track that left the
        playlist leaves its M3U too (issue #280). `quiet` keeps a scheduled
        check's success out of the toasts.
        """
        fmt = str(config.get("m3u_format") or "m3u8").lower()
        if fmt == "none":
            return {
                "ok": False,
                "error": "Playlist files are switched off in Settings (M3U format).",
            }
        if config.get("network_destination_id") or config.get("output_path"):
            return {
                "ok": False,
                "error": "Not available with a network destination or an exact "
                "output path.",
            }

        from .core.network_destinations import normalize_mounted_path
        from .core.playlist_sync import library_scan_dirs_from
        from .downloader import DownloadOptions, MornyeDownloader

        opts = DownloadOptions(
            output_dir=normalize_mounted_path(self.download_dir),
            filename_format=config.get("filename_format") or "{title} - {artist}",
            use_track_numbers=config.get("use_track_numbers") is True,
            use_album_track_numbers=config.get("use_album_track_numbers") is True,
            first_artist_only=config.get("first_artist_only") is True,
            create_playlist_subfolders=config.get("create_playlist_subfolders")
            is not False,
            m3u_format=fmt,
            library_scan_dirs=library_scan_dirs_from(config.get("library_scan_dirs")),
        )
        try:
            target, count = run_sync(
                MornyeDownloader.write_playlist_m3u_async(opts, playlist_name, tracks)
            )
        except Exception as exc:
            _quiet_log.warning("Could not write the playlist file", exc_info=True)
            return {"ok": False, "error": f"Could not write the playlist file: {exc}"}
        if target is None:
            return {
                "ok": False,
                "error": "None of this playlist's tracks are in the download "
                "folder yet: download it first.",
            }
        self.log(
            f"Playlist file written: {target} ({count} track(s))",
            "debug" if quiet else "ok",
        )
        return {"ok": True, "path": str(target), "found": count, "total": len(tracks)}

    def _download_job_payload(
        self, selected_indices, config, context: tuple | None = None
    ) -> dict | None:
        """One download request, in a form that outlives the process.

        The tracks themselves rather than their positions: `current_tracks`
        is replaced by the next fetch and empty after a restart, so indices
        alone would resume into nothing — or into another playlist. The
        indices ride along only so the page that asked can close the right
        rows when the batch ends. None when a selected track cannot be written
        down; the batch then runs the old, unpersisted way rather than not at
        all. `context` is ``(tracks, url, playlist_name)`` of the tracklist the
        indices point into; the current one when not given.
        """
        if context is None:
            context = (
                self.current_tracks,
                self.current_url or "",
                getattr(self, "current_playlist_name", ""),
            )
        listed, url, playlist_name = context
        try:
            indices = [int(i) for i in selected_indices]
            tracks = [listed[i].model_dump(mode="json") for i in indices]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        return {
            "indices": indices,
            "tracks": tracks,
            "source_url": url,
            # Decided now, against the tracklist the user was looking at:
            # after a restart there is no tracklist left to compare with.
            "whole": _covers_whole_list(indices, listed),
            # Only recorded with a failure (core/failed_tracks.py), never a
            # folder for this job: a whole playlist gets its folder from the
            # downloader, and "playlist_name" would nest it twice.
            "collection_name": playlist_name,
            "config": dict(config or {}),
            "session": self._session_id,
        }

    def _start_download_job(self, payload: dict) -> None:
        """Runs a self-contained download job: queued in --web mode, else now."""
        if self._download_queue is not None:
            self._submit_download_job(payload)
            return
        threading.Thread(
            target=self.run_download_job, args=(payload,), daemon=True
        ).start()

    def _submit_download_job(self, payload: dict) -> None:
        from .core.job_queue import QueueFullError

        try:
            queue = self._download_queue
            if queue is None:
                return
            payload = dict(payload)
            with self._active_download_events_lock:
                payload.setdefault("_stop_generation", self._stop_generation)
                queue.submit(self.owner, payload)
        except QueueFullError as exc:
            self.log(
                f"{exc.pending} downloads are already waiting (limit {exc.limit}); "
                "try again once some have finished.",
                "error",
            )

    def run_download_job(self, payload: dict) -> dict:
        """Runs one download job to the end, in the calling thread.

        The persisted queue's handler (see webapp.py), which is why it
        blocks: the job has to stay RUNNING until the batch is really over,
        or a restart halfway through would find it DONE and drop the rest.
        A job left over from an earlier process carries that process's
        session id and finishes as a background download, since no open page
        dispatched it (see app_background_download_finished in app.js).
        """
        retry_keys = payload.get("retry_keys")
        try:
            return self._run_download_job(payload)
        finally:
            if retry_keys:
                self._retries_in_flight().difference_update(retry_keys)

    def _run_download_job(self, payload: dict) -> dict:
        from .core.models import TrackMetadata

        indices = [int(i) for i in payload.get("indices") or []]
        raw_tracks = payload.get("tracks") or []
        if len(raw_tracks) != len(indices):
            msg = "Malformed download job: tracks and indices do not match"
            raise ValueError(msg)
        retry_keys = payload.get("retry_keys")
        if retry_keys is not None:
            # A retry runs only what is still listed. Its tracks were copied
            # into the job when it was queued; one cleared from the list (or
            # downloaded by another job) since then is dropped here, so a
            # Clear also empties the retries already waiting, those
            # restored after a restart included (issue #284).
            from .core import failed_tracks

            if len(retry_keys) != len(indices):
                msg = "Malformed retry job: keys and tracks do not match"
                raise ValueError(msg)
            listed = failed_tracks.listed_keys(getattr(self, "owner", "") or "")
            kept = [n for n, key in enumerate(retry_keys) if str(key) in listed]
            if not kept:
                return {"tracks": 0}
            raw_tracks = [raw_tracks[n] for n in kept]
            indices = list(range(len(kept)))
        expected_generation = payload.get(
            "_stop_generation", self._current_stop_generation()
        )
        if expected_generation != self._current_stop_generation():
            raise RuntimeError("Cancelled before starting.")
        tracks = {
            i: TrackMetadata.model_validate(t)
            for i, t in zip(indices, raw_tracks, strict=True)
        }
        playlist_tracks = [
            TrackMetadata.model_validate(track)
            for track in payload.get("playlist_tracks") or []
        ]
        self._download_task(
            indices,
            dict(payload.get("config") or {}),
            tracks_by_index=tracks,
            source_url=str(payload.get("source_url") or ""),
            whole=bool(payload.get("whole")),
            playlist_name=str(payload.get("playlist_name") or ""),
            playlist_tracks=playlist_tracks,
            collection_name=str(payload.get("collection_name") or ""),
            announce=payload.get("session") == self._session_id,
            is_retry=retry_keys is not None,
            expected_generation=expected_generation,
        )
        return {"tracks": len(indices)}

    def _await_download_slot(self, selected_indices) -> None:
        """Blocks until no other download batch is running.

        The frontend deliberately does not serialize its calls (see
        startDownloadQueue(): "do NOT block concurrent executions"), and
        every click on a track or an album cover starts another batch. Each
        one used to open its own event loop, its own provider set and its own
        semaphore, so "2 in parallel" quietly became 2 per batch — and worse,
        they fought over process-wide state: install_console_interception()
        and ProgressManager in core/progress.py are module-level, so the
        second run silently returned from the early-exit guard and the first
        one to finish put the console handlers back while the other was still
        printing.

        One session at a time, therefore. The batches still run, just in the
        order they arrived.
        """
        if self._download_lock.acquire(blocking=False):
            return
        self.log(
            f"Waiting for the running download to finish before starting "
            f"{len(selected_indices)} more track(s)…",
            "debug",
        )
        self._download_lock.acquire()

    def _current_stop_generation(self) -> int:
        with self._active_download_events_lock:
            return self._stop_generation

    def _download_task(
        self,
        selected_indices,
        config,
        *,
        expected_generation: int | None = None,
        **batch,
    ) -> None:
        """Runs one batch start to finish, never overlapping another one.

        The lock is taken and released here rather than around the body
        below: anything raising between acquiring it and reaching that
        method's own try/finally — the console reconfiguration, a handler
        that would not build — would otherwise leave it held, and every
        later download in the process would wait on it forever.
        """
        with self._active_download_events_lock:
            if (
                expected_generation is not None
                and expected_generation != self._stop_generation
            ):
                self.log("Download stopped before it started.", "warning")
                return
            stop_event = threading.Event()
            finish_event = threading.Event()
            self._active_download_events.add(stop_event)
            self._active_finish_events.add(finish_event)
        lock_acquired = False
        try:
            self._await_download_slot(selected_indices)
            lock_acquired = True
            if stop_event.is_set() or finish_event.is_set():
                self.log("Download stopped before it started.", "warning")
                return
            # Settings the page does not send (timeout_s, save_lrc, …) come
            # from MORNYE_<SETTING> when the container sets them; whatever
            # the page did send wins (core/env_settings.py).
            if isinstance(config, dict):
                from .core.env_settings import gui_env_defaults

                config = {**gui_env_defaults(), **config}
            self._run_download_batch(
                selected_indices,
                config,
                stop_event=stop_event,
                finish_event=finish_event,
                **batch,
            )
        finally:
            if lock_acquired:
                self._download_lock.release()
            with self._active_download_events_lock:
                self._active_download_events.discard(stop_event)
                self._active_finish_events.discard(finish_event)

    def stop_all_downloads(self) -> dict[str, int | bool]:
        """Stop current batches and cancel this account's queued downloads."""
        with self._active_download_events_lock:
            self._stop_generation += 1
            active = tuple(self._active_download_events)
            for event in active:
                event.set()
            queue = self._download_queue
            cancelled_pending = (
                queue.cancel_pending(getattr(self, "owner", "") or "")
                if callable(getattr(queue, "cancel_pending", None))
                else 0
            )
        self.log(
            f"Stopping {len(active)} active download(s); cancelled "
            f"{cancelled_pending} queued download(s).",
            "warning",
        )
        return {
            "ok": True,
            "stopped": len(active),
            "cancelled_pending": cancelled_pending,
        }

    def finish_current_downloads(self) -> dict[str, int | bool]:
        """Let the tracks downloading now finish, then stop.

        The gentle counterpart of stop_all_downloads(): nothing in flight is
        cut off, but no further track of a running batch is started, a batch
        still waiting for its turn never starts, and this account's queued
        downloads are cancelled.
        """
        with self._active_download_events_lock:
            # A batch that has not reached _download_task yet is caught by
            # the generation check there.
            self._stop_generation += 1
            active = tuple(self._active_finish_events)
            for event in active:
                event.set()
            queue = self._download_queue
            cancelled_pending = (
                queue.cancel_pending(getattr(self, "owner", "") or "")
                if callable(getattr(queue, "cancel_pending", None))
                else 0
            )
        self.log(
            f"Finishing the tracks in progress in {len(active)} download(s); "
            f"nothing else will start. Cancelled {cancelled_pending} queued "
            "download(s).",
            "warning",
        )
        return {
            "ok": True,
            "finishing": len(active),
            "cancelled_pending": cancelled_pending,
        }

    def _log_hires_verification(self, quality: str, redownload: bool) -> None:
        """Says what Hi-Res verification will actually do for this run.

        Three things can quietly make the setting a no-op, and all of them
        are invisible from the settings panel: the optional analysis
        packages are missing, the requested quality never claimed Hi-Res in
        the first place, or the check is on but set only to report. Saying
        so here costs one line and saves the user waiting for a warning
        that was never going to come.
        """
        from .core.hires_check import is_available
        from .core.quality import normalize_quality

        if not is_available():
            self.log(
                "Hi-Res verification is on but numpy/soundfile could not be "
                "imported. They ship with Mornye, so this points at a "
                "broken install rather than a missing extra. Downloads are "
                "unaffected; they simply will not be checked.",
                "warn",
            )
            return

        if normalize_quality(quality) not in ("HI_RES_LOSSLESS", "HI_RES"):
            self.log(
                f"Hi-Res verification is on but the requested quality is "
                f"{normalize_quality(quality)} — standard-definition content "
                "is what was asked for, so nothing will be flagged.",
                "debug",
            )
            return

        self.log(
            "Hi-Res verification enabled — each track is spectrally checked "
            "for upsampling"
            + (
                "; a flagged file is replaced by a LOSSLESS re-download"
                if redownload
                else " (findings are reported, files are left alone)"
            ),
            "debug",
        )

    def _run_download_batch(
        self,
        selected_indices,
        config,
        *,
        tracks_by_index: dict | None = None,
        source_url: str | None = None,
        whole: bool | None = None,
        playlist_name: str = "",
        playlist_tracks: list | None = None,
        collection_name: str | None = None,
        announce: bool = True,
        is_retry: bool = False,
        stop_event: threading.Event | None = None,
        finish_event: threading.Event | None = None,
    ) -> None:
        """One batch, start to finish.

        The keyword arguments come from a persisted job (see
        run_download_job), which carries its own tracks, source and
        whole-collection decision; without them the batch reads the tracklist
        on screen, as it always has. `announce=False` ends the batch with
        app_background_download_finished instead of app_download_finished.
        `is_retry` keeps the failure list from growing back: a retry only
        updates the rows still listed (core/failed_tracks.py).
        """
        self._download_active.set()
        from .client import _CleanConsoleFormatter

        sf_logger = logging.getLogger("mornye")
        handler = UILogHandler(self)
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        sf_logger.addHandler(handler)
        console_handler = logging.StreamHandler(sys.stdout)
        # Same formatter the CLI uses: an expected provider failure logged
        # with exc_info=True is one line here, not a traceback, until the
        # level really is DEBUG.
        console_handler.setFormatter(_CleanConsoleFormatter("  %(message)s"))
        sf_logger.addHandler(console_handler)
        # Ours is the handler that prints these, so stop the same record from
        # also reaching the root handler configured by
        # configure_console_logging() — that is what printed every library
        # line twice, once indented and once as "INFO: ...". Restored in the
        # finally below.
        sf_propagate = sf_logger.propagate
        sf_logger.propagate = False
        monitor_stop = None
        monitor_thread = None

        log_level_str = config.get("log_level", "INFO")
        current_log_level = logging.DEBUG if log_level_str == "DEBUG" else logging.INFO
        sf_logger.setLevel(current_log_level)
        # The picker in Settings is this GUI's --log-level, so it has to move
        # the console the process was started from too: the tracebacks and the
        # httpx chatter come from loggers we do not own.
        #
        # That reaches further than one batch, though: configure_console_logging
        # goes through basicConfig(force=True), which removes *and closes*
        # every handler the root logger already had. In --web mode that root
        # belongs to the host process, so one download left the server logging
        # through this batch's handler, at this batch's level, for the rest of
        # its life. Detached by hand first — removeHandler does not close —
        # so the saved handlers are still usable when the finally puts them
        # back.
        root_logger = logging.getLogger()
        prior_root_handlers = root_logger.handlers[:]
        prior_root_level = root_logger.level
        for prior_handler in prior_root_handlers:
            root_logger.removeHandler(prior_handler)
        configure_console_logging(current_log_level)

        destination_id = str(config.get("network_destination_id") or "").strip()
        smb_backend = None
        staging_dir = None
        download_output_dir = self.download_dir

        try:
            if destination_id:
                from .core.network_destinations import (
                    DESTINATIONS,
                    create_backend,
                    create_staging_directory,
                )

                profile = DESTINATIONS.get(destination_id)
                if profile is None:
                    raise ValueError(
                        "The selected network destination no longer exists."
                    )
                password = DESTINATIONS.credentials(profile, owner=self.owner)
                smb_backend = create_backend(profile, profile.username, password)
                smb_backend.test_connection()
                staging_dir = create_staging_directory(self.owner)
                download_output_dir = str(staging_dir)
            else:
                from .core.network_destinations import normalize_mounted_path

                download_output_dir = normalize_mounted_path(self.download_dir)
            os.makedirs(download_output_dir, exist_ok=True)

            quality = config.get("quality", "LOSSLESS")
            include_featuring = config.get("include_featuring", True)
            allow_fallback = config.get("allow_fallback", True)
            embed_lyrics = config.get("lyrics", True)
            enrich_metadata = config.get("enrich_metadata", True)
            raw_services = config.get("services", ["tidal", "qobuz", "deezer"])
            services = (
                [str(service) for service in raw_services if str(service).strip()]
                if isinstance(raw_services, (list, tuple))
                else []
            )
            preferred_service = str(
                config.get("preferred_service") or (services[0] if services else "")
            ).strip()
            fallback_services = config.get("fallback_services", True) is not False
            if preferred_service:
                from .extensions.catalog import canonical_service_name

                preferred_key = canonical_service_name(preferred_service)
                matching_service = next(
                    (
                        service
                        for service in services
                        if canonical_service_name(str(service)) == preferred_key
                    ),
                    None,
                )
                if matching_service is None:
                    raise ValueError(
                        "The preferred download provider is not enabled. "
                        "Choose an enabled provider in Settings."
                    )
                services = [matching_service] + [
                    service for service in services if service != matching_service
                ]
                if not fallback_services:
                    services = [matching_service]
            filename_format = config.get("filename_format", "{title} - {artist}")
            output_path = config.get("output_path") or None
            if destination_id and output_path:
                raise ValueError(
                    "Exact output paths cannot be used with a network destination; "
                    "use its subfolder and filename format."
                )
            use_track_numbers = config.get("use_track_numbers", False)
            use_album_track_numbers = config.get("use_album_track_numbers", False)
            use_artist_subfolders = config.get("use_artist_subfolders", False)
            use_album_subfolders = config.get("use_album_subfolders", False)
            album_folder_year = config.get("album_folder_year") is True
            # The CLI and the interactive picker both default this on;
            # the GUI had no toggle at all and never passed the option,
            # so client.Mornye's own False default applied and every
            # playlist landed loose in the download folder.
            create_playlist_subfolders = config.get("create_playlist_subfolders", True)
            library_dir: str | None = None
            if playlist_name.strip() and create_playlist_subfolders and not output_path:
                # The folder above is the whole library: these tracks are
                # looked up across every playlist folder in it first, so one
                # another playlist already has is linked from this one's M3U
                # rather than downloaded again (issue #267).
                library_dir = download_output_dir
                # Tracks picked out of a playlist (a followed playlist's
                # new ones) download as loose tracks; put them in the
                # folder the whole playlist would have gone into: the same
                # safe_dir_name() download_engine names that folder with, or
                # a name ending in "." or past the length cap would split
                # one playlist into two folders (issue #277).
                from .core.models import safe_dir_name

                download_output_dir = os.path.join(
                    download_output_dir, safe_dir_name(playlist_name)
                )
                os.makedirs(download_output_dir, exist_ok=True)
            first_artist_only = config.get("first_artist_only", False)
            artist_separator = config.get("artist_separator") or None
            lyrics_providers = config.get("lyrics_providers") or [
                "apple",
                "binilyrics",
                "lrclib",
            ]
            apple_lyrics_word_by_word = config.get("apple_lyrics_word_by_word", True)
            save_lrc = config.get("save_lrc", False)
            lrc_library_dir = config.get("lrc_library_dir") or None
            save_canvas = config.get("save_canvas", False)
            from .core.animated_artwork import (
                artwork_api_url,
                animated_artworks_enabled_by_default,
            )

            save_animated_artwork = (
                config.get(
                    "save_animated_artwork",
                    animated_artworks_enabled_by_default(),
                )
                is True
            )
            animated_artwork_api_url = (
                config.get("animated_artwork_api_url") or artwork_api_url()
            )
            canvas_library_dir = config.get("canvas_library_dir") or None
            canvas_providers = config.get("canvas_providers") or [
                "spotify",
                "paxsenix",
            ]
            enrich_providers = config.get("enrich_providers") or ["deezer", "apple"]
            from .core.transcode import is_lossless, normalize_transcode_format

            # The GUI sends "none" when conversion is disabled
            transcode_to = normalize_transcode_format(config.get("transcode_to"))
            transcode_bitrate = config.get("transcode_bitrate") or "320k"
            transcode_keep_original = config.get("transcode_keep_original", False)
            # On unless switched off with a real False; only a real False
            # lets the copy replace the original (see the note below).
            dolby_to_ac3 = config.get("dolby_to_ac3") is not False
            dolby_ac3_keep_original = config.get("dolby_ac3_keep_original") is not False
            # `is True`, not bool(): in --web mode this dict is an HTTP
            # request body, so a setting can arrive as the *string* "false"
            # — which bool() reads as on, and which would switch on a
            # feature that deletes files. Only a real boolean counts;
            # anything else, missing included, leaves it off.
            redownload_fake_hires = config.get("redownload_fake_hires") is True
            verify_hires = config.get("verify_hires") is True or redownload_fake_hires
            track_max_retries = int(config.get("track_max_retries", 0))
            # The GUI had no equivalent of --max-concurrent, so every
            # download ran at client.Mornye's default of 2 no matter what
            # the machine or the connection could take. Clamped rather than
            # trusted: this dict is an HTTP body in --web mode, and a
            # semaphore of 500 is a way to get every provider to rate-limit
            # the user at once.
            max_concurrent = max(
                1,
                min(8, int(config.get("max_concurrent_downloads", 2) or 2)),
            )
            post_download_action = config.get("post_download_action", "none")
            post_download_command = config.get("post_download_command", "")
            if post_download_action == "command" and not _post_command_allowed():
                # See _post_command_allowed(): "open_folder" and "notify" stay
                # available to the GUI unconditionally — only the shell action
                # needs the operator to have opted in when starting the process.
                self.log(
                    "Post-download command ignored: running a shell command from "
                    f"the interface is disabled. Set {POST_COMMAND_ENV}=1 before "
                    "starting Mornye to enable it.",
                    "error",
                )
                post_download_action = "none"
                post_download_command = ""
            # Per-service quality ids from the extensions' qualityOptions.
            # (qobuz_local_api_url / tidal_custom_api from older settings
            # files are ignored here: the page edits the extensions'
            # customEndpoint setting directly.)
            raw_extension_quality = config.get("extension_quality") or {}
            extension_quality = (
                {str(k): str(v) for k, v in raw_extension_quality.items() if k and v}
                if isinstance(raw_extension_quality, dict)
                else {}
            )
            resume = config.get("resume", True)
            timeout_s = config.get("timeout_s")
            loop_val = config.get("loop", None)
            loop_minutes = int(loop_val) if loop_val else None

            if not services:
                self.log("Error: select at least one service.", "error")
                return

            source = self.current_url if source_url is None else source_url
            collection_url = (source or "").strip()
            # A track list loaded from a CSV has no collection URL to stand
            # for it (see api_mixins/csv_import.py), so the whole-collection
            # shortcut only applies when there really is one.
            from_a_link = collection_url.startswith(("http", "spotify:"))
            # Link -> the metadata this GUI already fetched for that track,
            # handed to the downloader so it does not fetch every selected
            # track over again (a full lookup each). Only for a list read from
            # a link: a CSV row carries little more than a title, and the
            # lookup is what fills the rest in.
            prefetched: dict[str, TrackMetadata] = {}
            # Every track exactly once, not merely as many indices as tracks:
            # [0, 0, 1] out of three is not the whole collection, and in --web
            # mode this list is an HTTP body.
            whole_list = (
                whole
                if whole is not None
                else _covers_whole_list(
                    [int(i) for i in selected_indices], self.current_tracks
                )
            )
            if from_a_link and whole_list:
                urls_to_download = [collection_url]
                self.log("Downloading entire album/playlist…", "debug")
            else:
                urls_to_download = []
                unresolved: list[str] = []
                # core.tracklist.track_url, not a second copy of the same
                # id-to-link rules: this one matched on substrings of the
                # whole URL and minted `music.apple.com/track/{id}`, a path
                # Apple Music does not have and the parser rejects.
                from .core.models import TrackMetadata
                from .core.tracklist import track_url

                for i in selected_indices:
                    t = (
                        tracks_by_index[i]
                        if tracks_by_index is not None
                        else self.current_tracks[i]
                    )
                    t_url = track_url(t, source)
                    if t_url:
                        urls_to_download.append(t_url)
                        if from_a_link and isinstance(t, TrackMetadata):
                            prefetched[t_url] = t
                    else:
                        # Quiet: a tracklist that carries no links at all —
                        # a CSV of bare titles, say — hits this for every
                        # selected row, and one toast per row would be a
                        # wall of them. Counted into one toast below.
                        unresolved.append(t.title)
                        self.log(
                            f"Could not resolve URL for '{t.title}'. Skipping.",
                            "error-quiet",
                        )
                if unresolved:
                    self.log(
                        f"Skipped {len(unresolved)} track(s) with no resolvable "
                        "link — see the Logs view for which.",
                        "warn",
                    )

            if not urls_to_download:
                self.log("No valid URLs to download.", "error")
                return

            if transcode_to:
                # The bitrate only applies to the lossy targets: printing it
                # next to FLAC or ALAC would imply a knob that isn't there.
                detail = "lossless" if is_lossless(transcode_to) else transcode_bitrate
                self.log(
                    f"Transcoding enabled — tracks will be saved as "
                    f"{transcode_to.upper()} {detail}"
                    + ("" if transcode_keep_original else " (originals removed)"),
                    "debug",
                )

            if verify_hires:
                self._log_hires_verification(quality, redownload_fake_hires)

            self.set_progress(f"Downloading ({quality})…")
            monitor_stop = threading.Event()
            monitor_thread = threading.Thread(
                target=self._download_stats_monitor,
                args=(monitor_stop,),
                daemon=True,
            )
            monitor_thread.start()

            from .application import DownloadService, LegacyDownloadAdapter
            from .core.config import DownloadRequest, MornyeConfig
            from .core.download_log import record_hook
            from .core.playlist_sync import library_scan_dirs_from
            from .downloader import DownloadOptions

            # The same hook the CLI installs (see launcher._run_download_async).
            # Without it nothing a GUI or `--web` user downloaded was ever
            # written down: per-account quotas had nothing to count, and the
            # dashboard would have shown an empty history to precisely the
            # people who never touch the CLI.
            log_hook = record_hook(self.owner)
            # A track that fails stays listed, with what a retry needs, until
            # it downloads (core/failed_tracks.py): the downloader's own list
            # of failures ends with this call.
            from .core import failed_tracks

            if collection_name is None:
                collection_name = (
                    getattr(self, "current_playlist_name", "")
                    if source_url is None
                    else ""
                )
            failed_hook = failed_tracks.hook(
                self.owner,
                collection_url,
                playlist_name or collection_name,
                only_existing=is_retry,
            )
            deferred_download_hooks: list[tuple[Any, Any]] = []
            post_delivery_hooks = [log_hook, failed_hook]
            # Notifications configured by the environment (MORNYE_NOTIFY and
            # friends): a GUI or --web download has no --notify flag, and a
            # container is exactly where nobody watches the run (issue #254).
            notify_target, notify_report = self._notify_target_for_run()
            if notify_target is not None:
                if notify_report is not None:
                    post_delivery_hooks.append(notify_report)
                else:
                    from .core.notifiers import notify_hook

                    post_delivery_hooks.append(notify_hook(notify_target))
            if destination_id:

                async def defer_download_hook(result: Any, metadata: Any) -> None:
                    deferred_download_hooks.append((result, metadata))

                download_hooks = [defer_download_hook]
            else:
                download_hooks = cast(Any, post_delivery_hooks)

            # Request spacing (issue #279) as (min, max) seconds. A maximum
            # below the minimum, as "min 2" with max left at 0, means a fixed
            # delay of the minimum; DownloadConfig would refuse the pair and
            # every download of the batch would fail.
            def _delay_range(kind: str) -> tuple[float, float]:
                low = max(0.0, float(config.get(f"{kind}_delay_min_s") or 0.0))
                high = max(0.0, float(config.get(f"{kind}_delay_max_s") or 0.0))
                return low, max(low, high)

            metadata_delay = _delay_range("metadata")
            provider_delay = _delay_range("provider")

            legacy_opts = DownloadOptions(
                output_dir=download_output_dir,
                services=services,
                quality=quality,
                allow_fallback=allow_fallback,
                preferred_service=preferred_service or None,
                fallback_services=fallback_services,
                filename_format=filename_format,
                use_track_numbers=use_track_numbers,
                use_album_track_numbers=use_album_track_numbers,
                use_artist_subfolders=use_artist_subfolders,
                use_album_subfolders=use_album_subfolders,
                album_folder_year=album_folder_year,
                create_playlist_subfolders=create_playlist_subfolders,
                m3u_format=str(config.get("m3u_format") or "m3u8"),
                m3u_playlist_tracks=playlist_tracks or None,
                library_dir=library_dir,
                library_scan_dirs=library_scan_dirs_from(
                    config.get("library_scan_dirs")
                ),
                first_artist_only=first_artist_only,
                include_featuring=include_featuring,
                artist_separator=artist_separator,
                output_path=output_path,
                embed_lyrics=embed_lyrics,
                lyrics_providers=lyrics_providers,
                apple_lyrics_word_by_word=apple_lyrics_word_by_word,
                save_lrc=save_lrc,
                lrc_library_dir=lrc_library_dir,
                save_canvas=save_canvas,
                save_animated_artwork=save_animated_artwork,
                animated_artwork_api_url=animated_artwork_api_url,
                canvas_library_dir=canvas_library_dir,
                canvas_providers=canvas_providers,
                enrich_metadata=enrich_metadata,
                enrich_providers=enrich_providers,
                extension_quality=extension_quality,
                transcode_to=transcode_to,
                transcode_bitrate=transcode_bitrate,
                transcode_keep_original=transcode_keep_original,
                dolby_to_ac3=dolby_to_ac3,
                dolby_ac3_keep_original=dolby_ac3_keep_original,
                track_max_retries=track_max_retries,
                post_download_action=(
                    "none" if destination_id else post_download_action
                ),
                post_download_command=post_download_command,
                resume=resume,
                post_download_hooks=download_hooks,
                max_concurrent_downloads=max_concurrent,
                verify_hires=verify_hires,
                redownload_fake_hires=redownload_fake_hires,
                timeout_s=timeout_s,
                stop_event=stop_event,
                finish_event=finish_event,
                metadata_delay_min_s=metadata_delay[0],
                metadata_delay_max_s=metadata_delay[1],
                provider_delay_min_s=provider_delay[0],
                provider_delay_max_s=provider_delay[1],
                auto_rate_limit_cooldown=config.get("auto_rate_limit_cooldown", True)
                is True,
            )
            from .application.metadata_service import MetadataService

            service = DownloadService(
                provider_executor=LegacyDownloadAdapter.from_options(
                    legacy_opts,
                    prefetched=prefetched or None,
                ),
                stop_event=stop_event,
                # The real resolver. Left to its default, MetadataService
                # answers a spotify:track: link with its compatibility stub
                # ("Example Artist — Example Track"), and every provider was
                # then asked for a song that does not exist.
                metadata_service=MetadataService(
                    timeout_s=int(timeout_s or 10), compatibility_fallback=False
                ),
            )
            report = asyncio.run(
                service.download(
                    DownloadRequest(
                        sources=list(urls_to_download),
                        config=MornyeConfig.from_legacy_options(legacy_opts),
                        prefetched=prefetched or None,
                    )
                )
            )
            if smb_backend is not None and staging_dir is not None:
                self.set_progress("Delivering completed files to network destination…")
                self.log(
                    "Uploading completed files to the selected network destination.",
                    "info",
                )
                staged_files = sorted(
                    path for path in staging_dir.rglob("*") if path.is_file()
                )
                remote_paths = smb_backend.upload_tree(
                    staging_dir,
                    progress=lambda relative, current, total: self.set_progress(
                        f"Network transfer {relative}: {current}/{total} bytes"
                    ),
                )
                remote_by_local = dict(zip(staged_files, remote_paths, strict=True))
                if report is not None:
                    for result in report.succeeded:
                        local_path = (
                            Path(result.file_path) if result.file_path else None
                        )
                        if local_path not in remote_by_local:
                            from .core.network_destinations import DestinationError

                            raise DestinationError(
                                "A completed file was not found in network staging; "
                                "the staged files were retained."
                            )
                        result.file_path = remote_by_local[local_path]
                for result, _metadata in deferred_download_hooks:
                    if result.success and not result.skipped:
                        if not result.file_path:
                            from .core.network_destinations import DestinationError

                            raise DestinationError(
                                "A completed file has no staged path; "
                                "the staged files were retained."
                            )
                        remote_path = remote_by_local.get(Path(result.file_path))
                        if remote_path is None:
                            from .core.network_destinations import DestinationError

                            raise DestinationError(
                                "A completed file was not found in network staging; "
                                "the staged files were retained."
                            )
                        result.file_path = remote_path
                from .core.hooks import run_hooks

                async def run_deferred_download_hooks() -> None:
                    for result, metadata in deferred_download_hooks:
                        await run_hooks(post_delivery_hooks, result, metadata)

                run_sync(run_deferred_download_hooks())
                if post_download_action != "none":
                    from .application.batch_finalizer import BatchFinalizer

                    legacy_opts.post_download_action = post_download_action
                    failed_count = sum(
                        not result.success
                        for result, _metadata in deferred_download_hooks
                    )
                    skipped_count = sum(
                        result.skipped for result, _metadata in deferred_download_hooks
                    )
                    run_sync(
                        BatchFinalizer(
                            legacy_opts,
                            {},
                            [("", "", "", "")] * failed_count,
                            [("", "")] * skipped_count,
                            len(deferred_download_hooks),
                        ).execute_post_action(smb_backend.root)
                    )
                shutil.rmtree(staging_dir)
                self.log("Network delivery completed and verified.", "ok")
            if report is not None:
                self._push_download_stats()

            self._push_download_stats()
            if notify_target is not None and notify_report is not None:
                try:
                    from .core.notifiers import notify_run_summary

                    run_sync(notify_run_summary(notify_target, notify_report))
                except Exception as exc:
                    self.log(
                        f"Could not send the run notification: {exc}", "warn-quiet"
                    )
            nothing_saved = (
                report is not None
                and report.success_count == 0
                and (report.failed or report.total == 0)
            )
            if nothing_saved:
                # Say so: "All tracks saved" after zero downloads read as
                # success while the folder stayed empty.
                reason = (
                    report.failed[0].reason
                    if report.failed
                    else "no provider returned the track"
                )
                self.set_progress("Nothing downloaded.")
                self.log(f"Nothing was downloaded — {reason}", "error")
                with contextlib.suppress(Exception):
                    if announce:
                        self._push(
                            "app_download_finished", False, list(selected_indices)
                        )
                return
            self.set_progress("Complete!")
            self.log(f"All tracks saved to: {self.download_dir}", "ok")
            # The indices are echoed back so the frontend closes the
            # items of *this* batch: app_download_finished() used to mark
            # every 'active' row done, which with a second batch queued
            # behind this one meant marking tracks finished before they
            # had started. (_push never raises.)
            if announce:
                self._push("app_download_finished", True, list(selected_indices))
            else:
                self._push(
                    "app_background_download_finished", True, len(selected_indices)
                )

        except Exception as e:
            self.log(f"Download error: {e!s}", "error")
            if staging_dir is not None and staging_dir.exists():
                self.log(
                    "Local staged files were retained for retry. "
                    f"Retry identifier: {staging_dir.name.removeprefix('delivery-')}",
                    "warning",
                )
            self.set_progress("Error.")
            self._push_download_stats()
            if announce:
                self._push("app_download_finished", False, list(selected_indices))
            else:
                self._push(
                    "app_background_download_finished", False, len(selected_indices)
                )
            if destination_id and self._download_queue is not None:
                raise
        finally:
            if monitor_stop is not None:
                monitor_stop.set()
            if monitor_thread is not None:
                monitor_thread.join(timeout=1)
            self._push_download_stats()
            sf_logger.removeHandler(handler)
            if "console_handler" in locals():
                sf_logger.removeHandler(console_handler)
            sf_logger.propagate = sf_propagate
            # Root put back the way this batch found it, so the next request
            # starts from the host's configuration rather than from ours.
            for batch_handler in root_logger.handlers[:]:
                root_logger.removeHandler(batch_handler)
                with contextlib.suppress(Exception):
                    batch_handler.close()
            for prior_handler in prior_root_handlers:
                root_logger.addHandler(prior_handler)
            root_logger.setLevel(prior_root_level)
            self._download_active.clear()

    # ── Health Check ──────────────────────────────────────────────────────────

    def run_health_check(self, services) -> None:
        threading.Thread(
            target=self._health_check_task,
            args=(services,),
            daemon=True,
        ).start()

    def _download_stats_monitor(self, stop_event) -> None:
        try:
            from .core.progress import DownloadManager

            manager = DownloadManager()
            while not stop_event.wait(0.25):
                self._push_download_stats(manager.get_stats_sync())
        except Exception:
            _quiet_log.debug(
                "Download stats monitor stopped on an error", exc_info=True
            )
        finally:
            self._push_download_stats()

    def _push_download_stats(self, stats=None) -> None:
        try:
            if stats is None:
                from .core.progress import DownloadManager

                stats = DownloadManager().get_stats_sync()
            self._push("app_update_download_stats", stats)
        except Exception:
            _quiet_log.debug("Could not push the download stats", exc_info=True)

    def _health_check_task(self, services) -> None:
        """Run lyrics provider health checks and update the frontend."""
        try:
            from .core.health_check import run_health_check

            self.log(f"Health check started for: {', '.join(services)}", "debug")
            results = run_sync(run_health_check(services))
            data = [
                {
                    "provider": r.provider,
                    "method": r.method,
                    "url": r.url,
                    "ok": r.ok,
                    "latency": round(r.latency) if r.latency >= 0 else -1,
                    "detail": r.detail,
                }
                for r in results
            ]
            ok_providers = [r.provider for r in results if r.ok]
            self.log(
                f"Health check — {len([r for r in results if r.ok])}/{len(results)} endpoints OK.",
                "ok" if ok_providers else "error",
            )
            self._push("updateHealthResults", data)
        except ImportError:
            self.log("health_check module not found.", "error")
        except Exception as e:
            self.log(f"Health check error: {e!s}", "error")


#: Port the desktop window's bundled HTTP server prefers, so the page keeps
#: one origin (and therefore one localStorage) across launches. See the
#: comment at webview.start() below for why that matters.
GUI_HTTP_PORT = 47251


def _pick_gui_port() -> int | None:
    """`GUI_HTTP_PORT` when it is free, otherwise None (pick any port).

    Falling back rather than failing is deliberate: a busy port costs a
    stable origin — the window flashes light for a frame before app.js
    applies the stored theme — while refusing to start costs the whole
    application. The theme itself survives either way, because it is
    stored server-side in gui-settings.json.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", GUI_HTTP_PORT))
    except OSError:
        return None
    return GUI_HTTP_PORT


def _purge_webview_http_cache() -> None:
    """Drops the web view's persistent HTTP cache before the window opens.

    The desktop window serves the frontend over a *stable* origin
    (`http_server=True` on a fixed port) out of a *persistent* data store
    (`private_mode=False`) — both deliberate, see webview.start() below —
    and pywebview's bundled static server sends no cache headers at all.
    WebKit therefore takes index.html from its own cache on every launch,
    for as long as that entry lives.

    That is worse than it sounds: the `?v=` cache-bust that is supposed to
    make a frontend update visible lives *inside* index.html, on the
    <script>/<link> tags. A cached index.html keeps pointing at the old
    ?v= values, so the old app.js and stylesheets stay cached too, and the
    whole frontend freezes at whichever version WebKit saw first — through
    any number of restarts, and no matter how often the version is bumped.
    (Observed for real: a window still running a 1 September build days
    later, `it-IT` number formatting and all.)

    Wiping the cache costs one re-read of a few hundred KB from localhost
    on startup. Nothing else in this window is worth caching: every asset
    it loads is a local file. Best-effort — a cache that cannot be found
    or removed is not a reason to refuse to launch.
    """
    caches = Path.home() / "Library" / "Caches"  # macOS; other platforms no-op
    if not caches.is_dir():
        return

    bundle_id = "org.python.python"
    with contextlib.suppress(Exception):
        from Foundation import NSBundle  # pyobjc, always present with the cocoa backend

        bundle_id = NSBundle.mainBundle().bundleIdentifier() or bundle_id

    with contextlib.suppress(Exception):
        import shutil

        shutil.rmtree(
            caches / bundle_id / "WebKit" / "NetworkCache", ignore_errors=True
        )


def _enable_macos_first_click() -> None:
    """Make the first click on an inactive window act as a click (macOS).

    WKWebView answers NO to ``acceptsFirstMouse:``, so while Mornye is not
    the active app a click on the window only activates it and the button
    under the cursor never sees it: it took a second click. That happened
    whenever the window was not frontmost — after switching to another app,
    and at launch too, since macOS 14 treats ``activateIgnoringOtherApps``
    (what pywebview calls) as a request it may turn down, leaving the
    terminal in front. Answering YES lets one click both activate the
    window and reach the page. pywebview's WebKitHost is a PyObjC subclass
    of WKWebView, so the method is added to it before any window exists.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def acceptsFirstMouse_(self, event):  # noqa: N802 - Cocoa selector
            return True

        objc.classAddMethods(
            BrowserView.WebKitHost,
            [
                objc.selector(
                    acceptsFirstMouse_,
                    selector=b"acceptsFirstMouse:",
                    signature=b"Z@:@",
                )
            ],
        )
    except Exception:
        _quiet_log.debug("could not enable first-click on macOS", exc_info=True)


def _activate_macos_app() -> None:
    """Bring Mornye in front once its window is shown (macOS).

    pywebview's ``activateIgnoringOtherApps`` is ignored on macOS 14+, so
    the window could open behind the terminal without keyboard focus.
    ``NSApp.activate()`` is the call macOS 14+ honours.
    """
    if sys.platform != "darwin":
        return
    with contextlib.suppress(Exception):
        import AppKit
        from PyObjCTools import AppHelper

        def _activate():
            app = AppKit.NSApplication.sharedApplication()
            if hasattr(app, "activate"):
                app.activate()
            else:
                app.activateIgnoringOtherApps_(True)

        AppHelper.callAfter(_activate)


def run_gui() -> None:
    configure_console_logging(saved_log_level())
    _enable_macos_first_click()
    # Before pywebview creates the native app: from source the process is the
    # Python interpreter, so without this every OS shows Python's icon (and
    # on macOS its name) for the window. See desktop_branding.
    from .desktop_branding import apply_desktop_branding

    window_icon = apply_desktop_branding()
    _purge_webview_http_cache()
    api = MornyeAPI()

    # Try several candidate locations for the frontend files to be robust
    candidates = []

    # 1) frontend next to the installed module file (site-packages/frontend)
    site_packages_frontend = os.path.join(
        os.path.dirname(__file__),
        "frontend",
        "index.html",
    )
    candidates.append(site_packages_frontend)

    # 2) frontend inside the backend package (if present)
    try:
        import mornye as _sp_pkg

        pkg_frontend = os.path.join(
            os.path.dirname(_sp_pkg.__file__),
            "frontend",
            "index.html",
        )
        candidates.append(pkg_frontend)
    except Exception:
        _quiet_log.debug(
            "mornye package not importable while locating the frontend", exc_info=True
        )

    # 3) original heuristic (parent of site-packages, e.g. lib/pythonX.Y/frontend)
    original_frontend = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"),
    )
    candidates.append(original_frontend)

    # 4) installation data dir (some installers place data files under the install prefix)
    try:
        import sysconfig

        data_dir = sysconfig.get_paths().get("data")
        if data_dir:
            candidates.append(os.path.join(data_dir, "frontend", "index.html"))
    except Exception:
        _quiet_log.debug(
            "sysconfig data dir unavailable while locating the frontend", exc_info=True
        )

    # Pick the first existing candidate
    html_path = None
    for p in candidates:
        if os.path.exists(p):
            html_path = p
            break

    if not html_path:
        msg = f"index.html not found. Tried: {candidates}"
        raise FileNotFoundError(msg)

    window = webview.create_window(
        "Mornye",
        url=html_path,
        js_api=api,
        width=1300,
        height=850,
        min_size=(1000, 800),
        frameless=True,
        easy_drag=False,
        background_color="#0d0d0d",  # v2 ground, dark: no flash before CSS
    )
    api.set_window(window)
    if window is not None:
        window.events.loaded += api._on_loaded
        window.events.shown += _activate_macos_app
        if sys.platform == "win32":
            from .desktop_branding import apply_windows_window_icon

            window.events.shown += lambda: apply_windows_window_icon(window)

    # Two defaults conspired to make the theme picker look broken in the
    # desktop window, both of them about *where* the page's localStorage
    # lives:
    #
    #   private_mode=True (pywebview's default) throws the web view's
    #   storage away when the process exits, so 'mornye-theme-mode' was
    #   never there on the next launch.
    #
    #   http_server=True with no port serves index.html from a *random*
    #   free port, and http://127.0.0.1:51234 and http://127.0.0.1:51235 are
    #   different origins with different localStorage. So even within a
    #   single session's lifetime the storage was thrown away again on the
    #   next launch, private mode or not.
    #
    # The result was that picking Dark applied instantly and then came back
    # light on every restart. A fixed port plus a real storage directory
    # gives the page one stable origin whose contents survive, which is what
    # the pre-paint script in index.html reads. The theme no longer depends
    # on it either (save_theme() writes it to gui-settings.json — see
    # changeTheme() in frontend/app.js), but a stable origin is what keeps
    # the window from painting light for a frame first.
    storage_path = str(cache_path("webview"))
    with contextlib.suppress(Exception):
        Path(storage_path).mkdir(parents=True, exist_ok=True)

    try:
        webview.start(
            http_server=True,
            http_port=_pick_gui_port(),
            private_mode=False,
            storage_path=storage_path,
            icon=window_icon,
        )
    finally:
        api._close_native_player()


if __name__ == "__main__":
    run_gui()
