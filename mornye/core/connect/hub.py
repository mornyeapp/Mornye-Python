"""core/connect/hub.py — the devices of one account and which one plays.

One hub per scope: the desktop app has one (owner ``None``), ``--web`` one
per account (owner = the username), as ``ApiRegistry`` keeps one Api per
account. A device is anything that plays or remotes: the desktop window, a
browser tab, the server speaker, a cast receiver, a paired phone.

The hub holds no queue. The device that plays keeps its own (the page keeps
``playerQueue``; the server speaker its HeadlessPlayer) and *reports* its
state; the hub keeps the last one, picks the active device (whoever started
playing last, as Spotify Connect does: the others are paused), and routes a
remote's command to it. A transfer sends the active device's queue and
position to the target as ``play_queue`` and pauses the source.

Transport is injected, so this module knows nothing of pywebview, FastAPI or
Link:

- ``broadcast(fn_name, payload)`` reaches every page of the scope
  (``app_connect_devices``, ``app_connect_state``, and by default
  ``app_connect_command`` addressed by ``target``);
- a device may bring its own ``sender`` (a phone's WebSocket, the server
  speaker's player) to receive its commands;
- listeners hear every change (Link pushes ``player.state`` to phones from
  them).

Deliveries run outside the lock: a sender may call back into the hub.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .model import (
    CommandError,
    PlayerState,
    now_ms,
    state_changed,
    validate_command,
    validate_handoff,
)

logger = logging.getLogger(__name__)

Sender = Callable[[str, dict[str, Any]], None]
Broadcast = Callable[[str, Any], None]
Listener = Callable[[str, dict[str, Any]], None]

KINDS = ("window", "browser", "phone", "cast", "server", "mornye")

#: A page that stops reporting for this long is gone (it reports at least
#: every 15 s while playing; a remote's ``/ws`` keeps it present otherwise).
STALE_AFTER_S = 45.0


@dataclass
class Device:
    id: str
    name: str
    kind: str
    can_play: bool = True
    sender: Sender | None = None
    state: PlayerState | None = None
    seen: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    #: What only pages of the same account see next to the state (the file
    #: paths of the queue window, for artwork and for playing it there);
    #: never sent to a phone.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self, active_id: str | None, at_ms: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "canPlay": self.can_play,
            "active": self.id == active_id,
        }
        if self.state is not None:
            out["state"] = self.state.at(at_ms).to_json()
            if self.extra:
                out["extra"] = dict(self.extra)
        if self.meta:
            out["meta"] = dict(self.meta)
        return out


class ConnectHub:
    def __init__(
        self, owner: str | None = None, clock: Callable[[], float] = time.monotonic
    ):
        self.owner = owner
        self._clock = clock
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {}
        self._active: str | None = None
        self._broadcast: Broadcast | None = None
        self._listeners: list[Listener] = []

    # ── wiring ──
    def set_broadcast(self, fn: Broadcast | None) -> None:
        self._broadcast = fn

    def add_listener(self, fn: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def remove() -> None:
            with self._lock, contextlib.suppress(ValueError):
                self._listeners.remove(fn)

        return remove

    # ── devices ──
    def register(
        self,
        device_id: str,
        name: str,
        kind: str,
        *,
        sender: Sender | None = None,
        can_play: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        device_id = str(device_id or "").strip()
        if not device_id or len(device_id) > 128:
            raise CommandError("invalid", "a device id is required")
        if kind not in KINDS:
            raise CommandError("invalid", f"unknown device kind {kind!r}")
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                device = self._devices[device_id] = Device(device_id, "", kind)
            device.name = (name or "").strip()[:80] or device.name or kind.title()
            device.kind = kind
            device.can_play = can_play
            if sender is not None:
                device.sender = sender
            if meta is not None:
                device.meta = dict(meta)
            device.seen = self._clock()
        self._devices_changed()
        return self.snapshot()

    def touch(self, device_id: str) -> None:
        with self._lock:
            device = self._devices.get(device_id)
            if device is not None:
                device.seen = self._clock()

    def rename(self, device_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            device = self._require(device_id)
            device.name = (name or "").strip()[:80] or device.name
        self._devices_changed()
        return self.snapshot()

    def unregister(self, device_id: str) -> None:
        with self._lock:
            if self._devices.pop(device_id, None) is None:
                return
            if self._active == device_id:
                self._active = None
        self._devices_changed()

    def expire(self, max_age_s: float = STALE_AFTER_S) -> list[str]:
        """Drops the devices not seen for ``max_age_s`` (those with their own
        sender are kept: their transport says when they leave)."""
        cutoff = self._clock() - max_age_s
        with self._lock:
            gone = [
                d.id
                for d in self._devices.values()
                if d.sender is None and d.seen < cutoff
            ]
            for device_id in gone:
                del self._devices[device_id]
                if self._active == device_id:
                    self._active = None
        if gone:
            self._devices_changed()
        return gone

    def device(self, device_id: str) -> Device | None:
        with self._lock:
            return self._devices.get(device_id)

    @property
    def active_id(self) -> str | None:
        return self._active

    # ── state ──
    def report_state(
        self,
        device_id: str,
        raw: Any,
        extra: dict[str, Any] | None = None,
        *,
        claim: bool = True,
    ) -> bool:
        """A device's own player as it is now. Returns whether it changed
        enough to tell the others. A device that starts playing becomes the
        active one and every other playing device is paused — unless
        ``claim`` is false: a phone (or another computer) playing on its own
        pauses nothing, and becomes the active one only while nothing else
        plays (the computer pauses phones when it starts, not the other
        way)."""
        state = raw if isinstance(raw, PlayerState) else PlayerState.from_json(raw)
        if state is None:
            raise CommandError("invalid", "state is required")
        pauses: list[Device] = []
        active_changed = False
        with self._lock:
            device = self._require(device_id)
            device.seen = self._clock()
            changed = state_changed(device.state, state) or (
                extra is not None and extra != device.extra
            )
            started = state.playing and not (
                device.state is not None and device.state.playing
            )
            device.state = state
            if extra is not None:
                device.extra = dict(extra)
            if claim and state.playing and (started or self._active != device_id):
                # One device plays: whoever starts pauses the rest.
                pauses = [
                    d
                    for d in self._devices.values()
                    if d.id != device_id and d.state is not None and d.state.playing
                ]
                if self._active != device_id:
                    self._active = device_id
                    active_changed = True
            elif claim and self._active is None and state.track is not None:
                self._active = device_id
                active_changed = True
            elif (
                not claim
                and state.playing
                and self._active != device_id
                and not self._plays(self._active)
            ):
                # A phone or another computer playing on its own pauses
                # nothing, but when nothing else plays it is what the pages
                # show (a page opened now follows it), as in Spotify Connect.
                self._active = device_id
                active_changed = True
            is_active = self._active == device_id
        for other in pauses:
            self._deliver(other, "pause", {})
        if active_changed:
            self._devices_changed()
        if changed and is_active:
            payload = {"deviceId": device_id, "state": state.to_json()}
            self._emit("app_connect_state", {**payload, "extra": dict(device.extra)})
            self._notify("state", payload)
        return changed

    def active_state(
        self, at_ms: int | None = None
    ) -> tuple[str | None, PlayerState | None]:
        """The active device and its state as read now (fresh ``updatedMs``)."""
        with self._lock:
            device = self._devices.get(self._active or "")
            if device is None or device.state is None:
                return self._active, None
            return device.id, device.state.at(at_ms)

    # ── remote control ──
    def command(
        self, action: Any, args: Any = None, *, target: str | None = None
    ) -> dict:
        """Routes a remote's command to ``target`` (default: the active
        device), checked against the state that device last reported."""
        with self._lock:
            device_id = target or self._active
            if not device_id:
                raise CommandError("unavailable", "Nothing is playing")
            device = self._require(device_id)
            action, clean = validate_command(action, args, device.state)
        if action == "play_queue":
            clean = dict(args)  # delivered as sent: TrackRefs stay JSON
        self._deliver(device, action, clean)
        return {"ok": True}

    def transfer(
        self,
        target_id: str,
        *,
        handoff: dict[str, Any] | None = None,
        playing: bool | None = None,
    ) -> dict[str, Any]:
        """Moves playback to ``target_id``: the target gets ``play_queue``
        with the active device's queue at its current position, the source
        is paused. ``handoff`` replaces the queue the hub knows (a page sends
        its whole queue, not the 200-entry window)."""
        with self._lock:
            target = self._require(target_id)
            if not target.can_play:
                raise CommandError("invalid", f"{target.name} cannot play")
            source = self._devices.get(self._active or "")
            if source is not None and source.id == target.id:
                if handoff is None:
                    return self.snapshot()  # already playing there
                # A new queue for the device that already plays: a song or
                # an album picked on the other computer while this one
                # plays. Nothing to pause, nothing moves.
                source = None
            if handoff is None:
                if source is None or source.state is None:
                    raise CommandError("unavailable", "Nothing is playing")
                handoff = source.state.handoff_args(at_ms=now_ms())
            validate_handoff(handoff)
            args = dict(handoff)
            if playing is not None:
                args["playing"] = playing
            self._active = target.id
        if source is not None:
            self._deliver(source, "pause", {})
        self._deliver(target, "play_queue", args)
        self._devices_changed()
        return self.snapshot()

    # ── views ──
    def snapshot(self) -> dict[str, Any]:
        at = now_ms()
        with self._lock:
            devices = [d.to_json(self._active, at) for d in self._devices.values()]
            return {"activeId": self._active, "devices": devices}

    # ── internals ──
    def _plays(self, device_id: str | None) -> bool:
        device = self._devices.get(device_id or "")
        return device is not None and device.state is not None and device.state.playing

    def _require(self, device_id: str) -> Device:
        device = self._devices.get(device_id)
        if device is None:
            raise CommandError("not_found", "No such device")
        return device

    def _deliver(self, device: Device, action: str, args: dict[str, Any]) -> None:
        try:
            if device.sender is not None:
                device.sender(action, args)
            else:
                self._emit(
                    "app_connect_command",
                    {"target": device.id, "action": action, "args": args},
                )
        except Exception:
            logger.warning("Connect: could not reach %s", device.name, exc_info=True)

    def emit(self, fn_name: str, payload: Any) -> None:
        """Calls ``window.<fn_name>(payload)`` on every page of the account
        (what changed elsewhere: a phone edited a playlist)."""
        self._emit(fn_name, payload)

    def _emit(self, fn_name: str, payload: Any) -> None:
        fn = self._broadcast
        if fn is None:
            return
        try:
            fn(fn_name, payload)
        except Exception:
            logger.debug("Connect broadcast failed", exc_info=True)

    def _notify(self, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event, payload)
            except Exception:
                logger.debug("Connect listener failed", exc_info=True)

    def _devices_changed(self) -> None:
        snap = self.snapshot()
        self._emit("app_connect_devices", snap)
        self._notify("devices", snap)


_hubs: dict[str | None, ConnectHub] = {}
_hubs_lock = threading.Lock()


def get_connect_hub(owner: str | None = None) -> ConnectHub:
    """The hub of ``owner``'s devices (``None``: the desktop app, or
    single-user ``--web``)."""
    with _hubs_lock:
        hub = _hubs.get(owner)
        if hub is None:
            hub = _hubs[owner] = ConnectHub(owner)
        return hub


def reset_connect_hubs() -> None:
    """For tests."""
    with _hubs_lock:
        _hubs.clear()
