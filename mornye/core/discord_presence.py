"""core/discord_presence.py — "Listening to …" on the user's Discord profile.

Ported from the Mornye macOS app (DiscordPresence.swift). Discord's desktop
app takes Rich Presence over a local IPC channel — a Unix socket
``discord-ipc-N`` in the temporary folder, or the named pipe
``\\\\.\\pipe\\discord-ipc-N`` on Windows: a handshake carrying an application
ID, then ``SET_ACTIVITY`` commands, each framed as a little-endian opcode and
length in front of a JSON body. No Discord SDK and no network: when Discord
is not running there is nobody on the other end, and this tries again later.

The application ID is what Discord shows as the name: one made at
discord.com/developers and called "Mornye" reads "Listening to Mornye". It
is pasted in Settings › Player rather than built in.

The page tells this what plays (`now_playing`); a single worker thread owns
the connection, so a command and its reply are never interleaved with
another — which also keeps the Windows pipe, whose synchronous handle does
not take a read and a write at once, out of trouble.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .atomic_io import write_json_atomic
from .paths import cache_path
from .text_match import tag_artists

logger = logging.getLogger(__name__)

#: Seconds between attempts to reach Discord while it is closed.
RETRY_SECONDS = 15.0

#: A track change arrives as several updates; Discord accepts five
#: activities in twenty seconds, so they are gathered first.
COALESCE_SECONDS = 0.8

#: How far the start of a song may move, by a seek or the clock, before
#: Discord is told again.
DRIFT_SECONDS = 3.0

#: The largest frame read back; anything bigger is not Discord.
MAX_FRAME = 1_048_576

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4


# ── Settings ────────────────────────────────────────────────────────────────


@dataclass
class DiscordConfig:
    enabled: bool = False
    application_id: str = ""
    show_artwork: bool = True

    @property
    def valid_id(self) -> bool:
        return bool(self.application_id) and self.application_id.isdigit()


def _config_file():
    return cache_path("discord-presence.json")


def load_config() -> DiscordConfig:
    try:
        raw = json.loads(_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DiscordConfig()
    if not isinstance(raw, dict):
        return DiscordConfig()
    return DiscordConfig(
        enabled=bool(raw.get("enabled")),
        application_id=str(raw.get("application_id") or "").strip(),
        show_artwork=bool(raw.get("show_artwork", True)),
    )


def save_config(cfg: DiscordConfig) -> None:
    write_json_atomic(
        _config_file(),
        {
            "enabled": cfg.enabled,
            "application_id": cfg.application_id,
            "show_artwork": cfg.show_artwork,
        },
    )


# ── The wire ────────────────────────────────────────────────────────────────


def encode_frame(op: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", op, len(body)) + body


def candidate_paths() -> list[str]:
    """Where Discord may be listening, first match wins."""
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{i}" for i in range(10)]
    folders = [os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")]
    folders += [tempfile.gettempdir(), "/tmp"]
    # Flatpak and Snap builds of Discord put the socket one folder down.
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        folders += [
            os.path.join(runtime, "app", "com.discordapp.Discord"),
            os.path.join(runtime, "snap.discord"),
        ]
    seen: set[str] = set()
    out: list[str] = []
    for folder in folders:
        if not folder:
            continue
        folder = folder.rstrip("/") or "/"
        if folder in seen:
            continue
        seen.add(folder)
        out += [os.path.join(folder, f"discord-ipc-{i}") for i in range(10)]
    return out


class DiscordClosed(Exception):
    """The connection ended; `message` is Discord's reason when it gave one."""

    def __init__(self, message: str | None = None):
        super().__init__(message or "Discord closed the connection")
        self.message = message


class DiscordIpc:
    """One connection to the Discord client. Not thread-safe: the worker
    is its only user."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._pipe: Any = None

    # Connecting

    def open(self, application_id: str) -> str:
        """Connects and hands over the application ID; returns the Discord
        user's name. Raises OSError when Discord is not running and
        DiscordClosed when it refuses the ID."""
        last: OSError | None = None
        for path in candidate_paths():
            try:
                self._connect(path)
            except OSError as e:
                last = e
                continue
            try:
                self.send(OP_HANDSHAKE, {"v": 1, "client_id": application_id})
                while True:
                    op, body = self.receive()
                    if op == OP_FRAME and body.get("evt") == "READY":
                        user = (body.get("data") or {}).get("user") or {}
                        return str(
                            user.get("global_name") or user.get("username") or "Discord"
                        )
            except BaseException:
                self.close()
                raise
        raise last or OSError("Discord is not running")

    def _connect(self, path: str) -> None:
        if sys.platform == "win32":
            self._pipe = open(path, "r+b", buffering=0)  # noqa: SIM115
            return
        if not hasattr(socket, "AF_UNIX") or not os.path.exists(path):
            raise FileNotFoundError(path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(path)
        except OSError:
            sock.close()
            raise
        self._sock = sock

    def close(self) -> None:
        for handle in (self._sock, self._pipe):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        self._sock = None
        self._pipe = None

    # Frames

    def send(self, op: int, payload: dict) -> None:
        data = encode_frame(op, payload)
        try:
            if self._sock is not None:
                self._sock.sendall(data)
            elif self._pipe is not None:
                self._pipe.write(data)
                self._pipe.flush()
            else:
                raise DiscordClosed()
        except OSError as e:
            self.close()
            raise DiscordClosed() from e

    def _read_exactly(self, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            try:
                if self._sock is not None:
                    part = self._sock.recv(count - len(chunks))
                elif self._pipe is not None:
                    part = self._pipe.read(count - len(chunks))
                else:
                    part = b""
            except OSError as e:
                self.close()
                raise DiscordClosed() from e
            if not part:
                self.close()
                raise DiscordClosed()
            chunks += part
        return bytes(chunks)

    def receive(self) -> tuple[int, dict]:
        """The next frame Discord sends, answering pings on the way.
        Raises DiscordClosed on a close frame or a dropped connection."""
        while True:
            op, length = struct.unpack("<II", self._read_exactly(8))
            if length > MAX_FRAME:
                self.close()
                raise DiscordClosed("Discord sent a frame too large to read")
            raw = self._read_exactly(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            if op == OP_PING:
                self.send(OP_PONG, body)
                continue
            if op == OP_CLOSE:
                self.close()
                raise DiscordClosed(str(body.get("message") or "") or None)
            return op, body

    def set_activity(self, activity: dict | None) -> None:
        """Sends the activity (None clears it) and reads Discord's reply."""
        nonce = str(uuid.uuid4())
        self.send(
            OP_FRAME,
            {
                "cmd": "SET_ACTIVITY",
                "args": {"pid": os.getpid(), "activity": activity},
                "nonce": nonce,
            },
        )
        while True:
            _, body = self.receive()
            if body.get("nonce") == nonce:
                if body.get("evt") == "ERROR":
                    logger.info(
                        "[discord] activity refused: %s",
                        (body.get("data") or {}).get("message"),
                    )
                return


# ── The activity ────────────────────────────────────────────────────────────


def field(text: str) -> str:
    """Discord refuses strings under two characters or over 128."""
    text = str(text or "")[:128]
    return text + "\u2009\u2009" if len(text) < 2 else text


def normal(text: str) -> str:
    """Lower case, accents and punctuation gone: "Awaken, My Love!" and
    "awaken my love" are the same record."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).casefold()
    return re.sub(r"[\W_]+", "", value)


def build_activity(track: dict, start: float, artwork: str | None = None) -> dict:
    """The SET_ACTIVITY body for `track` (title, artist, album, duration)
    playing since the Unix time `start`."""
    activity: dict[str, Any] = {
        # 2 is "Listening to"; status_display_type 1 puts the artist in the
        # member list line instead of the application's name.
        "type": 2,
        "status_display_type": 1,
        "details": field(track.get("title") or ""),
    }
    artists = ", ".join(tag_artists(track.get("artist")))
    if artists:
        activity["state"] = field(artists)
    timestamps: dict[str, int] = {"start": int(start * 1000)}
    duration = float(track.get("duration") or 0)
    if duration > 0:
        timestamps["end"] = int((start + duration) * 1000)
    activity["timestamps"] = timestamps
    assets: dict[str, str] = {}
    if artwork:
        assets["large_image"] = artwork
    if track.get("album"):
        assets["large_text"] = field(track["album"])
    if assets:
        activity["assets"] = assets
    return activity


def lookup_artwork(
    artist: str, album: str, title: str, *, client: Any = None
) -> str | None:
    """The record's cover as a public URL Discord can show — a sleeve inside
    a local file is not one. Only a result whose artist matches: a wrong
    sleeve on a profile is worse than none. The album first, then the song,
    which finds singles and records the catalogue files under another
    title. Apple Music first, then Deezer, which has records Apple does not
    (Club Dogo's "Mi Fist")."""
    # The main artist of a multi-value tag: "Drake;Lil Durk" is filed under
    # Drake in the catalogues.
    artist = next(iter(tag_artists(artist)), "")
    if not artist:
        return None
    import httpx

    wanted = normal(artist)
    own = client is None
    http = client or httpx.Client(timeout=8.0, follow_redirects=True)

    def same_artist(name: Any) -> bool:
        found = normal(name or "")
        return bool(found) and (found in wanted or wanted in found)

    def results(url: str, params: dict, key: str) -> list:
        try:
            found = http.get(url, params=params).json().get(key) or []
        except Exception:
            return []
        return [r for r in found if isinstance(r, dict)]

    def apple(entity: str, name: str, term: str, matching: str) -> str | None:
        chosen = next(
            (
                r
                for r in results(
                    "https://itunes.apple.com/search",
                    {"term": f"{artist} {term}", "entity": entity, "limit": "10"},
                    "results",
                )
                if same_artist(r.get("artistName"))
                and normal(r.get(name) or "").startswith(matching)
            ),
            None,
        )
        url = (chosen or {}).get("artworkUrl100")
        return str(url).replace("100x100bb", "512x512bb") if url else None

    def deezer(kind: str, term: str, matching: str) -> str | None:
        path = "search/album" if kind == "album" else "search"
        for r in results(
            f"https://api.deezer.com/{path}",
            {"q": f"{artist} {term}", "limit": "10"},
            "data",
        ):
            record = r if kind == "album" else r.get("album") or {}
            if (
                isinstance(record, dict)
                and same_artist((r.get("artist") or {}).get("name"))
                and normal(r.get("title") or "").startswith(matching)
                and record.get("cover_xl")
            ):
                return str(record["cover_xl"])
        return None

    try:
        album_key, title_key = normal(album), normal(title)
        attempts = []
        if album:
            attempts.append(lambda: apple("album", "collectionName", album, album_key))
        if title:
            attempts.append(lambda: apple("song", "trackName", title, title_key))
        if album:
            attempts.append(lambda: deezer("album", album, album_key))
        if title:
            attempts.append(lambda: deezer("track", title, title_key))
        return next((url for attempt in attempts if (url := attempt())), None)
    finally:
        if own:
            http.close()


# ── The service ─────────────────────────────────────────────────────────────


def _seconds(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if 0 < number < 10**7 else 0.0


class DiscordPresence:
    """Keeps Discord showing what the Player plays. One per process."""

    def __init__(
        self,
        ipc_factory: Callable[[], DiscordIpc] = DiscordIpc,
        artwork: Callable[..., str | None] = lookup_artwork,
    ) -> None:
        self._ipc_factory = ipc_factory
        self._artwork = artwork
        self._artwork_cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._config = load_config()
        self._track: dict | None = None
        self._generation = 0
        self._status = "off"
        self._user = ""
        self._error = ""
        self._stopped = False

    # What the API sees

    def status(self) -> dict:
        with self._lock:
            cfg = self._config
            status = self._status
            if not cfg.enabled:
                status = "off"
            elif not cfg.valid_id:
                status = "missing_id"
            elif status == "off":
                status = "waiting"
            text = {
                "off": "Off",
                "missing_id": "Needs an application ID",
                "waiting": "Waiting for Discord to open…",
                "connected": f"Connected as {self._user}",
                "failed": self._error or "Discord refused the connection",
            }[status]
            return {
                "enabled": cfg.enabled,
                "application_id": cfg.application_id,
                "show_artwork": cfg.show_artwork,
                "status": status,
                "status_text": text,
                "connected": status == "connected",
            }

    def configure(
        self, *, enabled=None, application_id=None, show_artwork=None
    ) -> None:
        with self._lock:
            cfg = self._config
            if enabled is not None:
                cfg.enabled = bool(enabled)
            if application_id is not None:
                cfg.application_id = re.sub(r"\s+", "", str(application_id))
            if show_artwork is not None:
                cfg.show_artwork = bool(show_artwork)
            save_config(cfg)
            self._generation += 1
            self._status = "off"
            self._error = ""
        self.start()

    def now_playing(self, track: dict | None) -> None:
        """What plays now: title, artist, album, duration and position in
        seconds, and whether it is playing; None when nothing is."""
        state = None
        if isinstance(track, dict) and str(track.get("title") or "").strip():
            state = {
                "title": str(track.get("title") or "").strip(),
                "artist": str(track.get("artist") or "").strip(),
                "album": str(track.get("album") or "").strip(),
                "duration": _seconds(track.get("duration")),
                "start": time.time() - _seconds(track.get("position")),
                "playing": bool(track.get("playing")),
            }
        with self._lock:
            self._track = state
        self.start()

    def shutdown(self) -> None:
        """Closes the connection, which clears the profile, and ends the
        worker. For the app quitting and for tests."""
        with self._lock:
            self._stopped = True
            thread = self._thread
        self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def start(self) -> None:
        """Wakes the worker, starting it the first time it is needed."""
        with self._lock:
            if self._stopped:
                return
            if not self._config.enabled:
                self._wake.set()
                return
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="mornye-discord-presence", daemon=True
                )
                self._thread.start()
        self._wake.set()

    # The worker

    def _set_status(self, status: str, *, user: str = "", error: str = "") -> None:
        with self._lock:
            self._status = status
            if user:
                self._user = user
            self._error = error

    def _desired(self, cfg: DiscordConfig) -> tuple[str, dict | None]:
        """The key of what Discord should show, and the track (None: clear)."""
        with self._lock:
            track = dict(self._track) if self._track else None
        if not track or not track["playing"]:
            return "", None
        key = "|".join(
            [track["title"], track["artist"], track["album"], str(cfg.show_artwork)]
        )
        return key, track

    def _cover(self, track: dict) -> str | None:
        key = f"{track['artist']}|{track['album']}|{track['title']}".lower()
        if key not in self._artwork_cache:
            try:
                found = self._artwork(track["artist"], track["album"], track["title"])
            except Exception:
                found = None
            self._artwork_cache[key] = found or ""
        return self._artwork_cache[key] or None

    def _wait(self, seconds: float) -> None:
        if self._wake.wait(max(0.0, seconds)):
            self._wake.clear()
            # Gathers the rest of a burst of updates.
            time.sleep(COALESCE_SECONDS)
            self._wake.clear()

    def _run(self) -> None:
        ipc: DiscordIpc | None = None
        generation = -1
        sent_key: str | None = None
        sent_start = 0.0
        retry_at = 0.0
        while True:
            with self._lock:
                cfg = DiscordConfig(**vars(self._config))
                current = self._generation
                stopped = self._stopped
            if stopped:
                if ipc is not None:
                    ipc.close()
                return
            if current != generation:
                if ipc is not None:
                    ipc.close()
                ipc = None
                generation = current
                sent_key = None
                retry_at = 0.0
            if not cfg.enabled or not cfg.valid_id:
                if ipc is not None:
                    ipc.close()
                    ipc = None
                self._wait(60)
                continue

            if ipc is None:
                if time.monotonic() < retry_at:
                    self._wait(min(60.0, retry_at - time.monotonic()))
                    continue
                candidate = self._ipc_factory()
                try:
                    user = candidate.open(cfg.application_id)
                except DiscordClosed as e:
                    message = e.message or "Discord refused the connection"
                    self._set_status("failed", error=message)
                    # A bad application ID is not going to fix itself.
                    retry_at = (
                        float("inf")
                        if "client id" in message.lower()
                        else time.monotonic() + RETRY_SECONDS
                    )
                    continue
                except OSError:
                    self._set_status("waiting")
                    retry_at = time.monotonic() + RETRY_SECONDS
                    continue
                ipc = candidate
                sent_key = None
                self._set_status("connected", user=user)

            key, track = self._desired(cfg)
            # A seek of more than a few seconds moves the timestamps.
            moved = (
                track is not None and abs(track["start"] - sent_start) > DRIFT_SECONDS
            )
            if key != sent_key or moved:
                try:
                    if track is None:
                        ipc.set_activity(None)
                    else:
                        cover = self._cover(track) if cfg.show_artwork else None
                        if self._desired(cfg)[0] != key:
                            continue  # the song changed during the lookup
                        ipc.set_activity(build_activity(track, track["start"], cover))
                        sent_start = track["start"]
                    sent_key = key
                except DiscordClosed as e:
                    ipc = None
                    if e.message:
                        self._set_status("failed", error=e.message)
                    else:
                        self._set_status("waiting")
                    retry_at = time.monotonic() + RETRY_SECONDS
                    continue

            self._wait(60)


_service: DiscordPresence | None = None
_service_lock = threading.Lock()


def service() -> DiscordPresence:
    global _service
    with _service_lock:
        if _service is None:
            _service = DiscordPresence()
        return _service
