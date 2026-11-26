"""core/scrobbler.py — telling Last.fm what the Player played.

Ported from the Mornye macOS app (Scrobbler.swift). A song is scrobbled
once half of it, or four minutes, has been heard, and never one under
thirty seconds: Last.fm's own rule. The page measures the time heard
(core/listening.py) and says so when a song stops being the current one;
`listened` decides from that. While a song plays Last.fm also shows it as
"Scrobbling now" (`now_playing`), which it does not keep.

Signing in: Last.fm has no device flow. A request token is approved on
last.fm in the browser while Mornye asks every few seconds, and is then
exchanged for a session key, which does not expire. The API key and shared
secret are the user's own (last.fm/api/account/create) and, with the
session key, are kept in their own 0600 file, never in gui-settings.json.

Anything that fails to send waits in ``scrobble-queue.json`` and is tried
again: a flat network should not lose an evening of listening.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from .atomic_io import write_json_atomic
from .paths import cache_path

logger = logging.getLogger(__name__)

API_URL = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"

#: Last.fm's rule: at least this long a song, and heard for half of it or
#: for four minutes, whichever comes first.
MIN_TRACK_SECONDS = 30
ALWAYS_AFTER_SECONDS = 240

#: Asking whether the browser page was approved: every few seconds, for
#: two minutes.
AUTH_POLL_SECONDS = 3.0
AUTH_POLL_TRIES = 40

#: When nothing could be sent, how long until the next try.
RETRY_SECONDS = 300.0

#: Waiting scrobbles kept at most; the oldest go first.
QUEUE_LIMIT = 2000

#: Last.fm error codes worth trying again later (service offline,
#: temporarily unavailable, rate limited). 9 is a session that is no longer
#: valid. Any other answer will not change.
TEMPORARY_ERRORS = {11, 16, 29}
INVALID_SESSION = 9


# ── Settings ────────────────────────────────────────────────────────────────


@dataclass
class LastFmConfig:
    enabled: bool = False
    api_key: str = ""
    secret: str = ""
    session: str = ""
    user: str = ""


def _config_file():
    return cache_path("lastfm.json")


def _queue_file():
    return cache_path("scrobble-queue.json")


def load_config() -> LastFmConfig:
    try:
        raw = json.loads(_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LastFmConfig()
    if not isinstance(raw, dict):
        return LastFmConfig()
    return LastFmConfig(
        enabled=bool(raw.get("enabled")),
        api_key=str(raw.get("api_key") or ""),
        secret=str(raw.get("secret") or ""),
        session=str(raw.get("session") or ""),
        user=str(raw.get("user") or ""),
    )


def save_config(cfg: LastFmConfig) -> None:
    write_json_atomic(_config_file(), asdict(cfg), private=True)


# ── The protocol ────────────────────────────────────────────────────────────


def sign(params: dict[str, str], secret: str) -> str:
    """Last.fm signs a call with an MD5 of its parameters in name order,
    each name followed by its value, then the shared secret. MD5 is the
    protocol's, not a choice."""
    text = "".join(
        f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback")
    )
    return hashlib.md5(
        (text + secret).encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def should_scrobble(heard_seconds: float, duration_seconds: float) -> bool:
    """Last.fm's rule, from the time actually heard (never a seek)."""
    if duration_seconds > 0:
        if duration_seconds < MIN_TRACK_SECONDS:
            return False
        return heard_seconds >= min(duration_seconds / 2, ALWAYS_AFTER_SECONDS)
    # Without a length, only what was certainly long enough.
    return heard_seconds >= ALWAYS_AFTER_SECONDS


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if 0 < number < 10**10 else 0.0


# ── The service ─────────────────────────────────────────────────────────────


class LastFm:
    """Scrobbling to Last.fm. One per process."""

    def __init__(self, http: Callable[[dict], Any] | None = None):
        self._http = http
        self._lock = threading.RLock()
        self._config = load_config()
        self._pending: list[dict] = self._load_queue()
        self._authorising = False
        self._auth_error = ""
        self._auth_token = ""
        self._sending = False
        self._retry: threading.Timer | None = None
        self._now_playing_sent = ""

    # HTTP

    def _post(self, params: dict[str, str]) -> dict | None:
        """One signed call; the decoded answer, or None when Last.fm could
        not be reached."""
        with self._lock:
            secret = self._config.secret
        body = {k: v for k, v in params.items() if v != ""}
        body["api_sig"] = sign(body, secret)
        body["format"] = "json"
        try:
            if self._http is not None:
                answer = self._http(body)
            else:
                import httpx

                resp = httpx.post(
                    API_URL,
                    content=urlencode(body),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=15.0,
                )
                answer = resp.json()
        except Exception as e:
            logger.debug("[lastfm] call failed: %s", e)
            return None
        return answer if isinstance(answer, dict) else None

    def _signed(self, params: dict[str, str]) -> dict | None:
        """A call made on behalf of the signed-in user."""
        with self._lock:
            cfg = self._config
            if not (cfg.api_key and cfg.secret and cfg.session):
                return None
            params = {**params, "api_key": cfg.api_key, "sk": cfg.session}
        return self._post(params)

    # What the API sees

    @property
    def connected(self) -> bool:
        with self._lock:
            cfg = self._config
            return bool(cfg.enabled and cfg.session and cfg.api_key and cfg.secret)

    def status(self) -> dict:
        with self._lock:
            cfg = self._config
            if not cfg.enabled:
                text = "Off"
            elif self._authorising:
                text = "Waiting for approval in your browser…"
            elif cfg.session:
                text = f"Connected as {cfg.user or 'Last.fm'}"
            elif self._auth_error:
                text = self._auth_error
            elif not (cfg.api_key and cfg.secret):
                text = "Needs an API key and shared secret"
            else:
                text = "Not connected"
            return {
                "enabled": cfg.enabled,
                "api_key": cfg.api_key,
                "has_secret": bool(cfg.secret),
                "connected": self.connected,
                "user": cfg.user if cfg.session else "",
                "authorising": self._authorising,
                "status_text": text,
                "pending": len(self._pending),
            }

    def configure(self, *, enabled=None, api_key=None, secret=None) -> None:
        """Changes what is given; None leaves a setting as it is. A new API
        key or secret signs out: a session belongs to the key it was made
        with."""
        with self._lock:
            cfg = self._config
            if enabled is not None:
                cfg.enabled = bool(enabled)
            for name, value in (("api_key", api_key), ("secret", secret)):
                if value is None:
                    continue
                value = str(value).strip()
                if value != getattr(cfg, name):
                    setattr(cfg, name, value)
                    cfg.session = ""
                    cfg.user = ""
            self._auth_error = ""
            save_config(cfg)
        if self.connected:
            self.flush()

    def disconnect(self) -> None:
        with self._lock:
            self._config.session = ""
            self._config.user = ""
            self._auth_token = ""
            self._authorising = False
            save_config(self._config)

    # Signing in

    def begin_auth(self) -> str:
        """Asks Last.fm for a request token and returns the page where the
        user approves it; a thread then waits for the approval. Raises
        ValueError with a message for the user when it cannot start."""
        with self._lock:
            cfg = self._config
            if not (cfg.api_key and cfg.secret):
                raise ValueError("Needs an API key and shared secret")
            key = cfg.api_key
        answer = self._post({"method": "auth.getToken", "api_key": key})
        token = str((answer or {}).get("token") or "")
        if not token:
            if answer is None:
                raise ValueError("Could not reach Last.fm")
            raise ValueError(
                str(
                    answer.get("message")
                    or "Last.fm refused the API key or shared secret"
                )
            )
        with self._lock:
            self._auth_token = token
            self._authorising = True
            self._auth_error = ""
        threading.Thread(
            target=self._wait_for_approval,
            args=(key, token),
            name="mornye-lastfm-auth",
            daemon=True,
        ).start()
        return f"{AUTH_URL}?{urlencode({'api_key': key, 'token': token})}"

    def _wait_for_approval(self, key: str, token: str) -> None:
        try:
            for _ in range(AUTH_POLL_TRIES):
                time.sleep(AUTH_POLL_SECONDS)
                with self._lock:
                    if self._auth_token != token:
                        return  # cancelled, or another attempt began
                answer = self._post(
                    {"method": "auth.getSession", "api_key": key, "token": token}
                )
                session = (answer or {}).get("session") or {}
                if isinstance(session, dict) and session.get("key"):
                    with self._lock:
                        if self._auth_token != token:
                            return
                        self._config.session = str(session["key"])
                        self._config.user = str(session.get("name") or "")
                        self._auth_token = ""
                        save_config(self._config)
                    self.flush()
                    return
            with self._lock:
                if self._auth_token == token:
                    self._auth_token = ""
                    self._auth_error = "The request was not approved in time"
        finally:
            with self._lock:
                if self._auth_token == "":
                    self._authorising = False

    # Listening

    def now_playing(self, track: dict | None) -> None:
        """Shows the song as "Scrobbling now" while it plays."""
        if not isinstance(track, dict) or not track.get("playing"):
            return
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        if not title or not artist or not self.connected:
            return
        key = f"{title}|{artist}"
        with self._lock:
            if key == self._now_playing_sent:
                return
            self._now_playing_sent = key
        duration = int(_number(track.get("duration")))
        params = {
            "method": "track.updateNowPlaying",
            "artist": artist,
            "track": title,
            "album": str(track.get("album") or "").strip(),
            "duration": str(duration) if duration else "",
        }
        threading.Thread(
            target=self._signed, args=(params,), name="mornye-lastfm-now", daemon=True
        ).start()

    def listened(self, entry: dict, *, at: float | None = None) -> bool:
        """A song stopped being the current one (the page's final listening
        piece). Queues a scrobble when enough of it was heard; True if so."""
        if not isinstance(entry, dict):
            return False
        with self._lock:
            if not self._config.enabled:
                return False
            # The next play of the same song is "now playing" again.
            self._now_playing_sent = ""
        title = str(entry.get("title") or "").strip()
        artist = str(entry.get("artist") or "").strip()
        if not title or not artist:
            return False
        heard = _number(entry.get("heard"))
        duration = _number(entry.get("duration"))
        if not should_scrobble(heard, duration):
            return False
        now = float(at if at is not None else time.time())
        started = _number(entry.get("started")) or (now - heard)
        item = {
            "artist": artist,
            "track": title,
            "album": str(entry.get("album") or "").strip(),
            "duration": int(duration) if duration else 0,
            "timestamp": int(min(started, now)),
        }
        with self._lock:
            self._pending.append(item)
            del self._pending[:-QUEUE_LIMIT]
            self._save_queue()
        self.flush()
        return True

    # Sending

    def flush(self, *, wait: bool = False) -> None:
        """Sends what waits, oldest first, in a thread; `wait` for tests."""
        with self._lock:
            if self._sending or not self._pending or not self.connected:
                return
            self._sending = True
            if self._retry is not None:
                self._retry.cancel()
                self._retry = None
        thread = threading.Thread(
            target=self._send_all, name="mornye-lastfm-send", daemon=True
        )
        thread.start()
        if wait:
            thread.join()

    def _send_all(self) -> None:
        more = blocked = False
        try:
            with self._lock:
                batch = list(self._pending)
            done: list[dict] = []
            for item in batch:
                if self._send_one(item) == "keep":
                    blocked = True
                    break
                done.append(item)  # sent, or refused for good
            with self._lock:
                for item in done:
                    if item in self._pending:
                        self._pending.remove(item)
                self._save_queue()
                more = bool(self._pending)
        finally:
            with self._lock:
                self._sending = False
        if more and not blocked:
            self.flush()  # scrobbled while this batch was going
        elif more and self.connected:
            with self._lock:
                self._retry = threading.Timer(RETRY_SECONDS, self.flush)
                self._retry.daemon = True
                self._retry.start()

    def _send_one(self, item: dict) -> str:
        """'sent', 'drop' (refused for good) or 'keep' (try again later)."""
        answer = self._signed(
            {
                "method": "track.scrobble",
                "artist": item["artist"],
                "track": item["track"],
                "album": item.get("album") or "",
                "duration": str(item["duration"]) if item.get("duration") else "",
                "timestamp": str(item["timestamp"]),
            }
        )
        if answer is None:
            return "keep"
        code = answer.get("error")
        if code is None:
            return "sent"
        if code == INVALID_SESSION:
            with self._lock:
                self._config.session = ""
                self._config.user = ""
                self._auth_error = "Connect Last.fm again"
                save_config(self._config)
            return "keep"
        if code in TEMPORARY_ERRORS:
            return "keep"
        logger.info("[lastfm] scrobble refused (%s): %s", code, answer.get("message"))
        return "drop"

    # The waiting list

    @staticmethod
    def _load_queue() -> list[dict]:
        try:
            raw = json.loads(_queue_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [
            item
            for item in raw
            if isinstance(item, dict) and item.get("artist") and item.get("track")
        ][-QUEUE_LIMIT:]

    def _save_queue(self) -> None:
        try:
            if self._pending:
                write_json_atomic(_queue_file(), self._pending)
            else:
                _queue_file().unlink(missing_ok=True)
        except OSError:
            logger.debug("[lastfm] could not save the queue", exc_info=True)


_service: LastFm | None = None
_service_lock = threading.Lock()


def service() -> LastFm:
    global _service
    with _service_lock:
        if _service is None:
            _service = LastFm()
        return _service
