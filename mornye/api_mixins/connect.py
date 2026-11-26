"""api_mixins/connect.py — the page's side of Mornye Connect (core/connect).

A page (the desktop window, a browser on ``--web``) is a Connect device: it
registers, reports its player as it changes, and receives commands as
``window.app_connect_command({target, action, args})``; the device list and
the active device's state reach it as ``app_connect_devices`` and
``app_connect_state``.

The page speaks in its own queue entries (``{path, title, artist, album,
duration_s}``); this adapter turns them into TrackRefs (the library's song
id, ISRC, match key) on the way in, and resolves TrackRefs back into entries
for a page that is handed a queue. The file paths travel only between pages
of the same account (``extra.paths``), never to a phone.

Each account has its own hub (``self.owner`` in ``--web`` multi-user; the
desktop app and single-user ``--web`` share the ``None`` one), so these are
safe to expose over HTTP: an account sees and steers only its own devices.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.connect.hub import ConnectHub, get_connect_hub
from ..core.connect.model import MAX_QUEUE, CommandError, PlayerState, TrackRef, now_ms

logger = logging.getLogger(__name__)

_PAGE_REPEAT = {"off": "none", "none": "none", "all": "all", "one": "one"}


def _ok(**extra: Any) -> dict[str, Any]:
    return {"status": "ok", **extra}


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, CommandError):
        return {"status": "error", "code": exc.code, "error": exc.message}
    logger.warning("Connect call failed", exc_info=True)
    return {"status": "error", "code": "unavailable", "error": str(exc)}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class ConnectMixin:
    # Provided by MornyeAPI.
    _push: Any
    _window: Any

    # ── wiring ──
    def _connect_hub(self) -> ConnectHub:
        hub = get_connect_hub(getattr(self, "owner", "") or None)
        hub.set_broadcast(lambda fn, payload: self._push(fn, payload))
        return hub

    @staticmethod
    def _connect_catalog():
        from ..core.connect.resolver import catalog

        return catalog()

    def _connect_refs(self, entries: Any) -> list[TrackRef | None]:
        """Page entries (``{path, ...}``) or TrackRefs, as TrackRefs."""
        if not isinstance(entries, list):
            return []
        cat = None
        out: list[TrackRef | None] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path"):
                if cat is None:
                    cat = self._connect_catalog()
                out.append(cat.ref_for_item(entry))
            else:
                out.append(TrackRef.from_json(entry))
        return out

    def _connect_handoff(self, raw: Any) -> dict[str, Any]:
        """A page's handoff (``{items, index, positionMs, playing, shuffle,
        repeat}``), or one already in TrackRefs, in protocol form."""
        if not isinstance(raw, dict):
            raise CommandError("invalid", "a queue is required")
        entries = (
            raw.get("items") if isinstance(raw.get("items"), list) else raw.get("queue")
        )
        refs = self._connect_refs(entries)
        if not refs:
            raise CommandError("invalid", "the queue is empty")
        index = _number(raw.get("index")) or 0
        position = _number(raw.get("positionMs")) or 0
        out: dict[str, Any] = {
            "queue": [r.to_json() if r else {} for r in refs],
            "index": max(0, min(int(index), len(refs) - 1)),
            "positionMs": max(0, int(position)),
            "playing": raw.get("playing") is not False,
        }
        if isinstance(raw.get("shuffle"), bool):
            out["shuffle"] = raw["shuffle"]
        repeat = _PAGE_REPEAT.get(str(raw.get("repeat") or ""))
        if repeat:
            out["repeat"] = repeat
        return out

    # ── devices ──
    def connect_register(
        self, device_id: str, name: str = "", kind: str = "browser"
    ) -> dict:
        """Adds this page to the account's devices. Only the desktop window
        may call itself a window; any other page is a browser."""
        try:
            desktop = getattr(self, "_window", None) is not None
            kind = "window" if desktop and kind == "window" else "browser"
            if not (name or "").strip() and kind == "window":
                from ..link_server import computer_name

                name = computer_name()
            hub = self._connect_hub()
            hub.expire()
            self._connect_attach_server(hub)
            device_id = str(device_id or "")
            snap = hub.register(device_id, name or "", kind)
            device = hub.device(device_id)
            assert device is not None
            return _ok(
                device={"id": device.id, "name": device.name, "kind": device.kind},
                **snap,
            )
        except Exception as exc:
            return _error(exc)

    def connect_heartbeat(self, device_id: str) -> dict:
        try:
            hub = self._connect_hub()
            hub.touch(str(device_id or ""))
            hub.expire()
            return _ok(known=hub.device(str(device_id or "")) is not None)
        except Exception as exc:
            return _error(exc)

    def connect_unregister(self, device_id: str) -> dict:
        try:
            self._connect_hub().unregister(str(device_id or ""))
            return _ok()
        except Exception as exc:
            return _error(exc)

    def connect_rename(self, device_id: str, name: str) -> dict:
        try:
            return _ok(
                **self._connect_hub().rename(str(device_id or ""), str(name or ""))
            )
        except Exception as exc:
            return _error(exc)

    def connect_devices(self) -> dict:
        try:
            hub = self._connect_hub()
            hub.expire()
            self._connect_attach_server(hub)
            return _ok(**hub.snapshot())
        except Exception as exc:
            return _error(exc)

    # ── the server speaker (core/connect/speaker.py) ──
    def _connect_web(self) -> bool:
        return getattr(self, "_window", None) is None and bool(
            getattr(self, "_ws_broadcast", None)
        )

    def _connect_may_use_server(self) -> bool:
        """``--web`` only, and in multi-user mode admin accounts only: the
        server plays out loud where it stands."""
        if not self._connect_web():
            return False
        owner = getattr(self, "owner", "") or None
        if owner is None:
            return True
        from ..core.web_users import is_admin

        return is_admin(owner)

    def _connect_attach_server(self, hub: ConnectHub) -> None:
        if not self._connect_may_use_server():
            return
        from ..core.connect.speaker import attach_server_devices

        attach_server_devices(hub)

    def connect_speaker_status(self) -> dict:
        """Settings → Mornye Connect: the server speaker switch."""
        try:
            if not self._connect_web():
                return _ok(supported=False, enabled=False, available=False, reason="")
            from ..core.connect import speaker

            available, reason = speaker.speaker_available()
            return _ok(
                supported=True,
                enabled=speaker.speaker_enabled(),
                forced=speaker.forced_by_env(),
                available=available,
                reason=reason,
            )
        except Exception as exc:
            return _error(exc)

    def connect_speaker_set(self, enabled: bool) -> dict:
        """Turns the server speaker on or off (admin-only over HTTP:
        webapp.py's CONNECT_ADMIN_METHODS)."""
        try:
            if not self._connect_web():
                raise CommandError("invalid", "The server speaker is for --web only")
            from ..core.connect import speaker

            speaker.set_speaker_enabled(bool(enabled))
            if speaker.speaker_enabled():
                self._connect_attach_server(self._connect_hub())
            return self.connect_speaker_status()
        except Exception as exc:
            return _error(exc)

    # ── the page's own player ──
    def connect_report_state(self, device_id: str, state: dict) -> dict:
        """The page's player as it is now. ``items`` is the play order's
        window around the current entry. ``updatedMs`` is stamped here: a
        browser's clock may be anywhere."""
        try:
            if not isinstance(state, dict):
                raise CommandError("invalid", "state is required")
            raw_items = state.get("items")
            entries = raw_items[:MAX_QUEUE] if isinstance(raw_items, list) else []
            refs = self._connect_refs(entries)
            keep = [i for i, r in enumerate(refs) if r is not None]
            queue = tuple(r for r in refs if r is not None)
            raw_index = state.get("index")
            index = (
                keep.index(raw_index)
                if isinstance(raw_index, int) and raw_index in keep
                else -1
            )
            duration = _number(state.get("durationMs"))
            volume = _number(state.get("volume"))
            player = PlayerState(
                track=queue[index] if index >= 0 else None,
                playing=state.get("playing") is True,
                positionMs=max(0, int(_number(state.get("positionMs")) or 0)),
                durationMs=int(duration) if duration and duration > 0 else None,
                updatedMs=now_ms(),
                queue=queue,
                index=index,
                shuffle=state.get("shuffle") is True,
                repeat=_PAGE_REPEAT.get(str(state.get("repeat") or ""), "none"),
                volume=volume if volume is not None else 1.0,
            )
            paths = [
                (
                    str(entries[i].get("path") or "")
                    if isinstance(entries[i], dict)
                    else ""
                )
                for i in keep
            ]
            changed = self._connect_hub().report_state(
                str(device_id or ""), player, {"paths": paths}
            )
            return _ok(changed=changed)
        except Exception as exc:
            return _error(exc)

    # ── remote control ──
    def connect_command(
        self, action: str, args: Any = None, target: str | None = None
    ) -> dict:
        try:
            if action == "play_queue":
                args = self._connect_handoff(args)
            elif action == "queue_add" and isinstance(args, dict):
                entries = (
                    args.get("items")
                    if isinstance(args.get("items"), list)
                    else args.get("queue")
                )
                refs = [
                    r.to_json() for r in self._connect_refs(entries) if r is not None
                ]
                args = {"queue": refs, "next": args.get("next") is True}
            return _ok(
                **self._connect_hub().command(action, args, target=target or None)
            )
        except Exception as exc:
            return _error(exc)

    def connect_transfer(
        self, target: str, handoff: Any = None, playing: Any = None
    ) -> dict:
        """Moves playback to ``target``. A page that plays sends its whole
        queue as ``handoff``; without one the active device's window goes."""
        try:
            args = self._connect_handoff(handoff) if handoff else None
            snap = self._connect_hub().transfer(
                str(target or ""),
                handoff=args,
                playing=playing if isinstance(playing, bool) else None,
            )
            return _ok(**snap)
        except Exception as exc:
            return _error(exc)

    def connect_resolve(self, queue: Any) -> dict:
        """TrackRefs (a ``play_queue``'s queue) as this account's page plays
        them: one entry per ref, ``None`` where the library lacks the song,
        and those indexes in ``missing``."""
        try:
            from ..core.connect.resolver import received_files
            from ..core.link import link_dir

            refs = [
                TrackRef.from_json(q)
                for q in (queue if isinstance(queue, list) else [])
            ]
            received = received_files(link_dir() / "received")
            resolved = self._connect_catalog().resolve(refs, received)
            # Only what this account may play (the same rule prepare_playback
            # applies): in multi-user mode the library index is machine-wide.
            allowed = getattr(self, "_is_media_path", None)
            if allowed is not None:
                resolved = [r if r and allowed(r.path) else None for r in resolved]
            items = [r.item() if r else None for r in resolved]
            return _ok(
                items=items, missing=[i for i, r in enumerate(resolved) if r is None]
            )
        except Exception as exc:
            return _error(exc)
