"""api_mixins/cast.py — casting the Player to DLNA/UPnP and FCast receivers
(core/cast, ported from Mornye mobile 5.4.5).

Desktop only: these drive devices on the local network from the machine
running Mornye, so they stay out of webapp.py's ALLOWED_METHODS — over
`--web` a remote browser would be steering the server's LAN. The page keeps
the queue; the receiver's state comes back as `app_cast_status` events, the
device list as `app_cast_devices`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class CastMixin:
    if TYPE_CHECKING:
        _push: Any
        _is_media_path: Any
        log: Any

    def _cast(self):
        from ..core.cast import get_cast_controller

        def push(name: str, payload: Any) -> None:
            self._push(name, payload)

        return get_cast_controller(self._is_media_path, push)

    def cast_devices(self) -> dict:
        """The receivers found so far; discovery runs a minute more."""
        try:
            return {"status": "ok", "devices": self._cast().discover()}
        except Exception as e:
            return {"status": "error", "error": str(e), "devices": []}

    def cast_connect(self, device_id: str) -> dict:
        try:
            return {"status": "ok", "cast": self._cast().connect(str(device_id or ""))}
        except Exception as e:
            self.log(f"[cast] could not connect: {e}", "warning")
            return {"status": "error", "error": str(e)}

    def cast_load(
        self,
        path: str,
        meta: dict | None = None,
        start: float = 0,
        autoplay: bool = True,
    ) -> dict:
        try:
            return {
                "status": "ok",
                "cast": self._cast().load(
                    str(path or ""),
                    meta if isinstance(meta, dict) else {},
                    float(start or 0),
                    bool(autoplay),
                ),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def cast_command(self, name: str, value: Any = None) -> dict:
        try:
            return {
                "status": "ok",
                "cast": self._cast().command(str(name or ""), value),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def cast_status(self) -> dict:
        return {"status": "ok", "cast": self._cast().status.snapshot()}

    def cast_disconnect(self) -> dict:
        self._cast().disconnect()
        return {"status": "ok"}
