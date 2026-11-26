"""What the page is told about the cast receiver (Mornye mobile's status.rs).

One `Status` per session, shared by the session's threads under a lock;
`snapshot()` is what the frontend's cast engine reads (`app_cast_status`).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

UNKNOWN, IDLE, PLAYING, PAUSED, BUFFERING, LOADING = range(6)

FINISHED = "FINISHED"
ERROR = "ERROR"


@dataclass
class Device:
    """A receiver found on the network."""

    id: str
    name: str
    model: str
    host: str
    port: int
    kind: str  # "dlna" or "fcast"
    location: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "host": self.host,
            "port": self.port,
            "kind": self.kind,
        }


@dataclass
class Status:
    device: Device | None = None
    connected: bool = False
    revision: int = 0
    volume: float | None = None
    token: int | None = None
    current_time: float = 0.0
    reported: float | None = None
    duration: float = 0.0
    player_state: int = UNKNOWN
    idle_reason: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def start(self, device: Device, volume: float | None) -> None:
        with self.lock:
            self.device = device
            self.connected = True
            self.revision += 1
            self.volume = volume
            self.token = None
            self.current_time = 0.0
            self.reported = None
            self.duration = 0.0
            self.player_state = UNKNOWN
            self.idle_reason = None

    def begin_item(self, token: int, duration: float) -> None:
        with self.lock:
            self.token = token
            self.duration = max(0.0, duration)
            self.player_state = PAUSED
            self.idle_reason = None
            self.report(0.0)

    def report(self, seconds: float) -> None:
        with self.lock:
            self.current_time = max(0.0, seconds)
            self.reported = time.monotonic()

    def position_seconds(self) -> float:
        with self.lock:
            seconds = self.current_time
            if self.player_state == PLAYING and self.reported is not None:
                seconds += time.monotonic() - self.reported
            if self.duration > 0:
                seconds = min(seconds, self.duration)
            return seconds

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            device = self.device
            if device is None or not self.connected:
                out: dict[str, Any] = {"connected": False, "revision": self.revision}
                if device is not None:
                    out["kind"] = device.kind
                return out
            out = {
                "connected": True,
                "kind": device.kind,
                "deviceId": device.id,
                "revision": self.revision,
                "deviceName": device.name,
                "host": device.host,
                "token": self.token,
                "position": int(self.position_seconds() * 1000),
                "duration": int(self.duration * 1000),
                "state": self.player_state,
                "finished": self.idle_reason == FINISHED,
                "failed": self.idle_reason == ERROR,
            }
            if self.volume is not None:
                out["volume"] = self.volume
            return out
