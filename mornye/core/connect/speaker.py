"""core/connect/speaker.py — the machine running ``--web`` as Connect devices.

Off unless an admin turns it on (``MORNYE_CONNECT_SERVER_SPEAKER=1``, or
Settings → Mornye Connect, saved in ``connect.json``): with it on, a NAS or
a mini PC wired to an amplifier shows up in "Play on" as "<name> (server)"
and plays through mpv, the native player's engine, with the queue kept here
(``HeadlessPlayer``). In multi-user mode only admin accounts see it: it
plays out loud in the room the server is in, and from any file it can read.

The same switch lets the server cast: the DLNA and FCast receivers it finds
on its network become devices too, each a ``HeadlessPlayer`` whose engine
is the cast controller (``CastEngine``). The server serves them only the
library's files and the songs phones sent.

The desktop app never needs any of this — its own window plays and casts —
so none of it is created there.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ..atomic_io import write_json_atomic
from ..env import env_value
from ..paths import data_path
from .headless import HeadlessPlayer
from .model import TrackRef

logger = logging.getLogger(__name__)

SETTINGS_FILE = "connect.json"
DEVICE_ID = "server-speaker"

_TRUE = {"1", "true", "yes", "on"}
_lock = threading.Lock()
_speaker: HeadlessPlayer | None = None


def _settings() -> dict[str, Any]:
    try:
        raw = json.loads(data_path(SETTINGS_FILE).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def forced_by_env() -> bool:
    return (
        env_value("MORNYE_CONNECT_SERVER_SPEAKER", "") or ""
    ).strip().lower() in _TRUE


def speaker_enabled() -> bool:
    return forced_by_env() or _settings().get("serverSpeaker") is True


def set_speaker_enabled(on: bool) -> None:
    settings = _settings()
    settings["serverSpeaker"] = bool(on)
    write_json_atomic(data_path(SETTINGS_FILE), settings)
    if not speaker_enabled():
        close_server_speaker()
        close_cast_receivers()


def speaker_available() -> tuple[bool, str]:
    """Whether mpv can play here (libmpv is found)."""
    from ..native_player import NativePlayerUnavailable, load_mpv

    try:
        load_mpv()
        return True, ""
    except NativePlayerUnavailable as exc:
        return False, str(exc)


def _resolve(refs: list[TrackRef | None]) -> list[dict | None]:
    from ..link import link_dir
    from .resolver import catalog, received_files

    resolved = catalog().resolve(refs, received_files(link_dir() / "received"))
    return [r.item() if r else None for r in resolved]


def _refs_for(items: list[dict]) -> list[TrackRef | None]:
    from .resolver import catalog

    cat = catalog()
    return [cat.ref_for_item(item) for item in items]


def _mpv_engine(emit):
    from ..native_player import NativePlayer

    # No media keys or Now Playing: nobody sits at a server's keyboard.
    return NativePlayer(emit, media_controls=False)


def get_server_speaker() -> HeadlessPlayer | None:
    """The server speaker when it is on and mpv is there, else None."""
    global _speaker
    if not speaker_enabled():
        return None
    with _lock:
        if _speaker is None:
            available, reason = speaker_available()
            if not available:
                logger.warning("Server speaker unavailable: %s", reason)
                return None
            from ...link_server import computer_name

            _speaker = HeadlessPlayer(
                DEVICE_ID,
                f"{computer_name()} (server)",
                "server",
                _mpv_engine,
                resolve=_resolve,
                refs_for=_refs_for,
            )
        return _speaker


def close_server_speaker() -> None:
    global _speaker
    with _lock:
        speaker, _speaker = _speaker, None
    if speaker is not None:
        speaker.close()


# ── Cast receivers (core/cast) ──────────────────────────────────────────────

_receivers: dict[str, HeadlessPlayer] = {}
_engines: dict[str, CastEngine] = {}
_server_hubs: list[Any] = []


def _cast_allowed(path: Any) -> bool:
    """What the server's cast media server may hand a receiver: a library
    file or a song a phone sent, nothing else on the disk."""
    from pathlib import Path

    from ..link import link_dir
    from .resolver import catalog

    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    if resolved.is_relative_to((link_dir() / "received").resolve()):
        return True
    return catalog().song_for_path(str(path)) is not None or (
        catalog().song_for_path(str(resolved)) is not None
    )


def _controller():
    from ..cast import get_cast_controller

    return get_cast_controller(_cast_allowed, _cast_push)


def _cast_push(name: str, payload: Any) -> None:
    if name == "app_cast_devices":
        _sync_receivers(payload if isinstance(payload, list) else [])
    elif name == "app_cast_status" and isinstance(payload, dict):
        engine = _engines.get(str(payload.get("deviceId") or ""))
        if engine is None:
            # A lost connection names no device: the receiver that had it.
            engine = next((e for e in _engines.values() if e.connected), None)
        if engine is not None:
            engine.receive(payload)


class CastEngine:
    """A receiver as a HeadlessPlayer engine: loads go through the cast
    controller (one receiver connected at a time), and its status snapshots
    come back as the native player's events."""

    def __init__(self, device_id: str, emit) -> None:
        self.device_id = device_id
        self.emit = emit
        self.connected = False
        self._token: Any = None
        self._meta: dict[str, Any] = {}
        self._ended_token: Any = None
        self._playing = False
        self._duration = 0
        _engines[device_id] = self

    def describe(self, item: dict) -> None:
        self._meta = {
            "title": item.get("title") or "",
            "artist": item.get("artist") or "",
            "album": item.get("album") or "",
            "duration": item.get("duration_s") or 0,
        }

    def load(self, path: str, next_path: str | None = None) -> None:
        controller = _controller()
        if not self.connected:
            controller.connect(self.device_id)
            self.connected = True
        snap = controller.load(path, self._meta, 0.0, True)
        self._token = snap.get("token")
        self._ended_token = None

    def set_next(self, path: str | None) -> None:
        """Receivers take one song at a time: the queue moves on at "ended"."""

    def command(self, name: str, value: Any = None) -> None:
        if not self.connected:
            return
        if name in ("play", "pause", "seek", "volume"):
            _controller().command(name, value)

    def close(self) -> None:
        _engines.pop(self.device_id, None)
        if self.connected:
            self.connected = False
            try:
                _controller().disconnect()
            except Exception:
                logger.debug("cast disconnect failed", exc_info=True)

    def receive(self, snap: dict) -> None:
        if not snap.get("connected"):
            if self.connected:
                self.connected = False
                self._playing = False
                self.emit({"type": "pause"})
                self.emit({"type": "error", "message": "The receiver went away"})
            return
        if self._token is not None and snap.get("token") not in (None, self._token):
            return  # an older song's news
        time_s = (snap.get("position") or 0) / 1000
        duration = snap.get("duration") or 0
        if duration and duration != self._duration:
            self._duration = duration
            self.emit({"type": "loadedmetadata", "duration": duration / 1000})
        if snap.get("failed"):
            self.emit(
                {"type": "error", "message": "The receiver could not play this song"}
            )
        if snap.get("finished"):
            if self._ended_token != self._token:
                self._ended_token = self._token
                self._playing = False
                self.emit({"type": "ended"})
            return
        state = snap.get("state")
        if state == 2 and not self._playing:
            self._playing = True
            self.emit({"type": "play", "time": time_s})
        elif state in (1, 3) and self._playing:
            self._playing = False
            self.emit({"type": "pause", "time": time_s})
        else:
            self.emit({"type": "timeupdate", "time": time_s})


def _receiver_player(device: dict) -> HeadlessPlayer:
    device_id = str(device["id"])
    kind = "FCast" if device.get("kind") == "fcast" else "DLNA"
    return HeadlessPlayer(
        f"cast:{device_id}",
        f"{device.get('name') or 'Receiver'} · {kind}",
        "cast",
        lambda emit: CastEngine(device_id, emit),
        resolve=_resolve,
        refs_for=_refs_for,
    )


def _sync_receivers(devices: list[dict]) -> None:
    with _lock:
        if not speaker_enabled():
            return
        seen = {str(d.get("id")) for d in devices if d.get("id")}
        added = []
        for device in devices:
            device_id = str(device.get("id") or "")
            if device_id and device_id not in _receivers:
                _receivers[device_id] = _receiver_player(device)
                added.append(_receivers[device_id])
        gone = [p for i, p in _receivers.items() if i not in seen and not p.playing]
        for player in gone:
            _receivers.pop(player.id.removeprefix("cast:"), None)
        hubs = list(_server_hubs)
    for player in added:
        for hub in hubs:
            player.attach(hub)
    for player in gone:
        player.close()


def attach_server_devices(hub: Any) -> None:
    """Puts the server speaker and the receivers the server can cast to in
    ``hub`` (an admin's, or the single-user one), and looks for more."""
    speaker = get_server_speaker()
    if speaker is None:
        return
    speaker.attach(hub)
    with _lock:
        if hub not in _server_hubs:
            _server_hubs.append(hub)
        receivers = list(_receivers.values())
    for player in receivers:
        player.attach(hub)
    try:
        _sync_receivers(_controller().discover())
    except Exception:
        logger.debug("cast discovery failed", exc_info=True)


def close_cast_receivers() -> None:
    with _lock:
        players = list(_receivers.values())
        _receivers.clear()
        _server_hubs.clear()
    for player in players:
        player.close()
