"""The page's handle on the native audio engine (core/native_player.py).

Desktop window only. These methods are deliberately absent from webapp.py's
ALLOWED_METHODS: in ``--web`` the music must play on the listener's device,
and over HTTP they would let anyone who reaches the server play files on
its speakers and list its audio devices.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..core.native_player import (
    NativePlayer,
    NativePlayerUnavailable,
    format_filter_list,
    load_options,
    parse_filter_list,
)

logger = logging.getLogger(__name__)

_COMMANDS = {"play", "pause", "seek", "volume", "mute", "speed", "ab_loop", "stop"}
_DESKTOP_ONLY = "The native player runs in the desktop app only."


class NativePlayerMixin:
    """`native_player_*` methods; events reach the page as
    ``window.app_native_player_event(event)``."""

    _native_player: NativePlayer | None = None
    _native_player_lock = threading.Lock()

    # Provided by MornyeAPI / LibraryToolsMixin.
    _window: Any
    _is_approved_path: Any
    _push: Any

    def _desktop(self) -> bool:
        return getattr(self, "_window", None) is not None

    def _native(self) -> NativePlayer:
        if not self._desktop():
            raise NativePlayerUnavailable(_DESKTOP_ONLY)
        with self._native_player_lock:
            if self._native_player is None:
                self._native_player = NativePlayer(
                    lambda event: self._push("app_native_player_event", event)
                )
            return self._native_player

    @staticmethod
    def _native_call(fn) -> dict:
        try:
            return {"status": "ok", **(fn() or {})}
        except NativePlayerUnavailable as exc:
            return {"status": "unavailable", "error": str(exc)}
        except (ValueError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            logger.warning("native player call failed", exc_info=True)
            return {"status": "error", "error": str(exc)}

    def native_player_status(self) -> dict:
        """Whether the native engine can play here, and its stored options."""
        if not self._desktop():
            return {"status": "ok", "available": False, "reason": _DESKTOP_ONLY}
        return self._native_call(lambda: self._native().status())

    def native_player_load(self, path: str, next_path: str | None = None) -> dict:
        """Plays `path` now, with `next_path` queued for a gapless change."""

        def run():
            if not isinstance(path, str) or not self._is_approved_path(path):
                raise ValueError("Path is outside approved directories")
            nxt = (
                next_path
                if isinstance(next_path, str) and self._is_approved_path(next_path)
                else None
            )
            self._native().load(path, nxt)

        return self._native_call(run)

    def native_player_set_next(self, path: str | None = None) -> dict:
        """Replaces the file queued after the current one (None: nothing)."""

        def run():
            nxt = (
                path if isinstance(path, str) and self._is_approved_path(path) else None
            )
            self._native().set_next(nxt)

        return self._native_call(run)

    def native_player_command(self, name: str, value: Any = None) -> dict:
        def run():
            if name not in _COMMANDS:
                raise ValueError(f"Unknown command: {name}")
            self._native().command(name, value)

        return self._native_call(run)

    def native_player_devices(self) -> dict:
        return self._native_call(lambda: {"devices": self._native().devices()})

    def native_player_set_options(self, options: dict) -> dict:
        """Stores the Player audio settings and applies them at once."""
        return self._native_call(
            lambda: {"options": self._native().set_options(options).to_dict()}
        )

    def native_player_eq_import(self, text: str) -> dict:
        """Reads an AutoEQ-style filter list (``Preamp:`` and ``Filter N:``
        lines) into the parametric equalizer and switches it on."""

        def run():
            if not isinstance(text, str) or len(text) > 64_000:
                raise ValueError("The filter list must be plain text.")
            preamp, filters = parse_filter_list(text)
            options = self._native().set_options(
                {
                    "eq_enabled": True,
                    "eq_mode": "parametric",
                    "eq_preamp": preamp,
                    "eq_filters": filters,
                }
            )
            return {"options": options.to_dict(), "imported": len(filters)}

        return self._native_call(run)

    def native_player_eq_export(self) -> dict:
        """The parametric equalizer as an AutoEQ-style text filter list."""

        def run():
            if not self._desktop():
                raise NativePlayerUnavailable(_DESKTOP_ONLY)
            options = load_options()
            return {"text": format_filter_list(options.eq_preamp, options.eq_filters)}

        return self._native_call(run)

    def native_player_stats(self) -> dict:
        return self._native_call(lambda: {"stats": self._native().stats()})

    def _close_native_player(self) -> None:
        """Stops mpv when the window closes: python-mpv's event thread is
        not a daemon, and a live one would keep the process from exiting."""
        with self._native_player_lock:
            player, self._native_player = self._native_player, None
        if player is not None:
            player.close()
