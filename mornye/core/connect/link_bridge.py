"""core/connect/link_bridge.py — Mornye Link's player ops, answered by Connect.

The phone app drives "the desktop player" through Link (mornyeapp/Mornye,
``link_desktop_ops.dart``, ``link_desktop_host.dart``): ``GET state``,
``POST command``, ``handoff/play``, ``handoff/append``, ``handoff/current``,
``queue/add``, ``player/details``, ``resolve``, ``PUT files/<id>.<ext>``, and
over the socket ``player.state`` both ways and ``command`` to the phone.

Here "the desktop player" is the account's Connect hub (the desktop app's,
or an admin's in multi-user ``--web``): the device that plays, unless that is
a phone, else the desktop window. Each connected phone is a Connect device
too (``phone:<deviceId>``): it shows in "Play on", its own ``player.state``
is kept, and the hub reaches it with ``command`` frames ("Continue on
phone" is a ``play_queue``; the computer starting to play sends ``pause``).
A phone playing on its own never takes over the computer.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .hub import ConnectHub, get_connect_hub
from .model import (
    CommandError,
    PlayerState,
    TrackRef,
    is_file_id,
    now_ms,
    state_changed,
    validate_handoff,
)

logger = logging.getLogger(__name__)

#: A state push waits this long for more changes (``link_desktop_host.dart``).
PUSH_DEBOUNCE_S = 0.25
#: While something plays, the state is sent again this often.
HEARTBEAT_S = 15.0
#: An uploaded song may be at most this big.
MAX_UPLOAD = 1 << 30
AUDIO_EXTENSIONS = {
    "flac", "mp3", "m4a", "mp4", "aac", "ogg", "opus", "wav", "aiff", "aif", "wv", "alac",
}  # fmt: skip

PHONE_PREFIX = "phone:"


def phone_device_id(device_id: str) -> str:
    return PHONE_PREFIX + device_id


class LinkConnect:
    """One per Link server. ``send(device_id, message)`` writes a frame to a
    phone's socket from any thread; ``connected()`` lists the phones with a
    socket; ``owner()`` names the hub to use."""

    def __init__(
        self,
        send: Callable[[str, dict[str, Any]], None],
        connected: Callable[[], list[str]],
        *,
        received_dir: Callable[[], Path],
        catalog: Callable[[], Any],
        owner: Callable[[], str | None] = lambda: None,
        loop: Callable[[], asyncio.AbstractEventLoop | None] = lambda: None,
    ) -> None:
        self._send = send
        self._connected = connected
        self._owner = owner
        self._received_dir = received_dir
        self._catalog = catalog
        self._loop = loop
        self._lock = threading.Lock()
        self._hub: ConnectHub | None = None
        self._unlisten: Callable[[], None] | None = None
        self._last_sent: PlayerState | None = None
        self._push_timer: Any = None
        self._beat: threading.Timer | None = None
        self._handoff_id = ""

    # ── the hub ──
    def hub(self) -> ConnectHub:
        hub = get_connect_hub(self._owner())
        with self._lock:
            if hub is not self._hub:
                if self._unlisten is not None:
                    self._unlisten()
                self._hub = hub
                self._unlisten = hub.add_listener(self._on_hub)
        return hub

    def target(self) -> tuple[str | None, PlayerState | None]:
        """The player a phone sees as "the desktop": the active device unless
        it is a phone, else the desktop window, else any page with a state."""
        hub = self.hub()
        snap = hub.snapshot()
        devices = snap["devices"]
        active = next((d for d in devices if d["id"] == snap["activeId"]), None)
        if active is None or active["kind"] == "phone":
            ranked = sorted(
                (d for d in devices if d["kind"] != "phone" and d["canPlay"]),
                key=lambda d: (d["kind"] != "window", "state" not in d),
            )
            active = ranked[0] if ranked else None
        if active is None:
            return None, None
        device = hub.device(active["id"])
        state = device.state.at() if device and device.state else None
        return active["id"], state

    def state_json(self) -> dict[str, Any]:
        """``GET state``: read now, so ``updatedMs`` is fresh (the phone
        estimates the clock offset from it)."""
        _, state = self.target()
        return (state or PlayerState(updatedMs=now_ms())).to_json()

    # ── phones as devices ──
    def phone_connected(self, device_id: str, name: str) -> None:
        def sender(action: str, args: dict[str, Any]) -> None:
            self._send(device_id, {"type": "command", "action": action, "args": args})

        self.hub().register(phone_device_id(device_id), name, "phone", sender=sender)
        self._send(device_id, {"type": "player.state", "state": self.state_json()})

    def phone_disconnected(self, device_id: str) -> None:
        self.hub().unregister(phone_device_id(device_id))

    def phone_message(self, device_id: str, message: Any) -> None:
        """A frame from a phone: its own ``player.state`` is kept."""
        if not isinstance(message, dict) or message.get("type") != "player.state":
            return
        state = PlayerState.from_json(message.get("state"))
        if state is None:
            return
        try:
            self.hub().report_state(phone_device_id(device_id), state.at(), claim=False)
        except CommandError:
            pass

    # ── pushes ──
    def _on_hub(self, event: str, payload: dict[str, Any]) -> None:
        self.schedule_push()

    def schedule_push(self) -> None:
        """A push after ``PUSH_DEBOUNCE_S`` of quiet (now, without a loop)."""
        loop = self._loop()
        if loop is None or not loop.is_running():
            self.push()
            return

        def arm() -> None:
            if self._push_timer is not None:
                self._push_timer.cancel()
            self._push_timer = loop.call_later(PUSH_DEBOUNCE_S, self._push_soon)

        loop.call_soon_threadsafe(arm)

    def _push_soon(self) -> None:
        self._push_timer = None
        threading.Thread(
            target=self.push, name="link-connect-push", daemon=True
        ).start()

    def push(self, force: bool = False) -> None:
        """Sends ``player.state`` to every phone when it changed enough (or
        always, for the heartbeat)."""
        phones = self._connected()
        if not phones:
            return
        _, state = self.target()
        state = state or PlayerState(updatedMs=now_ms())
        with self._lock:
            if not force and not state_changed(self._last_sent, state):
                return
            self._last_sent = state
        frame = {"type": "player.state", "state": state.to_json()}
        for device_id in phones:
            self._send(device_id, frame)
        self._arm_heartbeat(state.playing)

    def _arm_heartbeat(self, playing: bool) -> None:
        with self._lock:
            if self._beat is not None:
                self._beat.cancel()
                self._beat = None
            if playing:
                self._beat = threading.Timer(
                    HEARTBEAT_S, self.push, kwargs={"force": True}
                )
                self._beat.daemon = True
                self._beat.start()

    def close(self) -> None:
        with self._lock:
            if self._beat is not None:
                self._beat.cancel()
                self._beat = None
            if self._unlisten is not None:
                self._unlisten()
                self._unlisten = None
            self._hub = None

    # ── ops ──
    def op(self, op: str, method: str, args: dict[str, Any], device_id: str) -> Any:
        """One ``/mornye/v1/<op>``; raises CommandError with a Link code."""
        handlers: dict[tuple[str, str], Callable[[dict[str, Any], str], Any]] = {
            ("state", "GET"): lambda a, d: self.state_json(),
            ("command", "POST"): self._command,
            ("handoff/play", "POST"): self._handoff_play,
            ("handoff/append", "POST"): self._handoff_append,
            ("handoff/current", "GET"): self._handoff_current,
            ("queue/add", "POST"): self._queue_add,
            ("player/details", "POST"): self._details,
            ("resolve", "POST"): self._resolve,
        }
        handler = handlers.get((op, method))
        if handler is None:
            raise CommandError("not_found", f"No such op: {op}")
        return handler(args, device_id)

    def _require_target(self) -> str:
        target, _ = self.target()
        if target is None:
            raise CommandError("unavailable", "Mornye is not open on this computer")
        return target

    def _command(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        action = args.get("action")
        if not isinstance(action, str) or not action:
            raise CommandError("invalid", "action is required")
        raw = args.get("args")
        command_args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if action == "play_queue":
            # The phone handing its queue over, as handoff/play does.
            self._handoff_play(command_args, device_id)
            return {"ok": True}
        target = self._require_target()
        if action == "pause":
            # "Continue on phone" pauses here; the phone then plays itself.
            command_args = {}
        self.hub().command(action, command_args, target=target)
        return {"ok": True}

    def _resolve_refs(
        self, queue: list[Any], arriving: list[dict[str, Any]] | None = None
    ) -> list[Any]:
        from .resolver import received_files

        catalog = self._catalog()
        if catalog is None:
            raise CommandError("unavailable", "The catalog is not ready")
        refs = [
            r if isinstance(r, TrackRef) or r is None else TrackRef.from_json(r)
            for r in queue
        ]
        received = received_files(self._received_dir())
        for a in arriving or []:
            stem = str(a.get("name", "")).split(".", 1)[0]
            if is_file_id(stem):
                received.setdefault(stem, str(self._received_dir() / a["name"]))
        return catalog.resolve(refs, received)

    def _handoff_play(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        clean = validate_handoff(args)
        resolved = self._resolve_refs(clean["queue"], clean.get("arriving"))
        missing = [i for i, r in enumerate(resolved) if r is None]
        out: dict[str, Any] = {
            "resolved": len(resolved) - len(missing),
            "missing": missing,
        }
        if clean.get("check") or out["resolved"] == 0:
            return out
        target = self._require_target()
        handoff = {k: v for k, v in args.items() if k not in ("check", "arriving")}
        handoff.setdefault("playing", True)
        self.hub().transfer(target, handoff=handoff)
        self._handoff_id = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        out["handoffId"] = self._handoff_id
        return out

    def _handoff_append(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        if not self._handoff_id or args.get("handoffId") != self._handoff_id:
            raise CommandError("invalid", "That hand-off is over")
        return self._queue_add({"queue": args.get("queue"), "next": False}, device_id)

    def _queue_add(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        queue = args.get("queue")
        if not isinstance(queue, list):
            raise CommandError("invalid", "queue is required")
        resolved = self._resolve_refs(queue)
        found = [q for q, r in zip(queue, resolved, strict=False) if r is not None]
        if found:
            self.hub().command(
                "queue_add",
                {"queue": found, "next": args.get("next") is True},
                target=self._require_target(),
            )
        return {"resolved": len(found)}

    def _handoff_current(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        _, state = self.target()
        if state is None:
            return {
                "queue": [],
                "index": -1,
                "positionMs": 0,
                "playing": False,
                "shuffle": False,
                "repeat": "none",
            }
        return state.handoff_args()

    def _details(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        ref = TrackRef.from_json(args.get("track"))
        if ref is None:
            raise CommandError("invalid", "track is required")
        catalog = self._catalog()
        song = catalog.song_for_ref(ref) if catalog is not None else None
        if song is None:
            raise CommandError("unavailable", "That song is not in this library")
        out: dict[str, Any] = {"metadata": {}, "album": song.album}
        if song.suffix:
            out["format"] = song.suffix.upper()
        if song.genre:
            out["genre"] = song.genre
        return out

    def _resolve(self, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        tracks = args.get("tracks")
        if not isinstance(tracks, list):
            raise CommandError("invalid", "tracks is required")
        catalog = self._catalog()
        if catalog is None:
            raise CommandError("unavailable", "The catalog is not ready")
        ids: list[str | None] = []
        for raw in tracks:
            ref = TrackRef.from_json(raw)
            song = catalog.song_for_ref(ref) if ref else None
            ids.append(song.id if song is not None else None)
        return {"songIds": ids}

    # ── uploads ──
    def upload_path(self, name: str) -> Path:
        """Where ``PUT files/<name>`` goes: ``<32 hex>.<audio ext>`` only."""
        stem, _, ext = name.partition(".")
        if not is_file_id(stem) or ext.lower() not in AUDIO_EXTENSIONS or "/" in name:
            raise CommandError("invalid", "The name must be <32 hex>.<audio extension>")
        return self._received_dir() / f"{stem}.{ext.lower()}"
