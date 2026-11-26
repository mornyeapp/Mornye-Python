"""Finding cast receivers and playing on one (the controller of the split
FLACintosh's Cast uses: discovery → local HTTP serving → controller).

* Discovery: SSDP rounds for DLNA/UPnP MediaRenderers (each answer's device
  description is read once to know it can play audio), and an mDNS browser
  for FCast receivers (``_fcast._tcp``). It runs while the device picker
  asks for devices, and stops a minute after the last ask.
* Serving: a file is published on a media server bound to the address the
  receiver reaches this computer at (core/media_server.py
  `get_cast_media_server`), under a random name, with Range, HEAD and the
  DLNA headers renderers check.
* Control: one session at a time (cast/dlna.py, cast/fcast.py). The page
  keeps the queue, as with the native engine: it loads a song, the session
  reports its state, and when the receiver says the song finished the
  page's own "ended" moves to the next one.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..media_server import content_type_for, get_cast_media_server
from . import ssdp
from .dlna import DlnaSession, parse_description
from .fcast import SERVICE_TYPE as FCAST_SERVICE
from .fcast import FcastSession, device_from_service
from .status import Device, Status

logger = logging.getLogger(__name__)

#: How long discovery keeps running after the picker last asked.
DISCOVERY_LINGER = 60.0
#: Time between SSDP rounds.
ROUND = 15.0
#: A renderer not heard from for this long is dropped.
STALE = 50.0
#: A description that could not be read is asked again after this.
RETRY = 30.0

Push = Callable[[str, Any], None]


class CastController:
    def __init__(self, is_allowed: Callable[[Path], bool], push: Push) -> None:
        self.is_allowed = is_allowed
        self.push = push
        self.lock = threading.RLock()
        self._devices: dict[str, Device] = {}
        self._seen: dict[str, float] = {}
        self._failed: dict[str, float] = {}
        self._wanted_until = 0.0
        self._discovery: threading.Thread | None = None
        self._zeroconf: Any = None
        self.session: DlnaSession | FcastSession | None = None
        self.status = Status()
        self._token = 0
        self.last_error = ""

    # ── discovery ───────────────────────────────────────────────────────

    def devices(self) -> list[dict]:
        with self.lock:
            return sorted(
                (d.public() for d in self._devices.values()),
                key=lambda d: d["name"].lower(),
            )

    def discover(self) -> list[dict]:
        """The devices found so far; keeps discovery running a minute more."""
        with self.lock:
            self._wanted_until = time.monotonic() + DISCOVERY_LINGER
            if self._discovery is None or not self._discovery.is_alive():
                self._discovery = threading.Thread(
                    target=self._discover, name="mornye-cast-discovery", daemon=True
                )
                self._discovery.start()
        return self.devices()

    def _changed(self) -> None:
        try:
            self.push("app_cast_devices", self.devices())
        except Exception:
            logger.debug("[cast] could not push devices", exc_info=True)

    def upsert(self, device: Device) -> None:
        with self.lock:
            changed = self._devices.get(device.id) != device
            self._devices[device.id] = device
            self._seen[device.id] = time.monotonic()
        if changed:
            self._changed()

    def remove(self, device_id: str) -> None:
        with self.lock:
            gone = self._devices.pop(device_id, None) is not None
            self._seen.pop(device_id, None)
        if gone:
            self._changed()

    def _discover(self) -> None:
        self._start_fcast()
        try:
            while time.monotonic() < self._wanted_until:
                start = time.monotonic()
                try:
                    self.handle_ssdp(ssdp.search())
                except Exception:
                    logger.debug("[cast] SSDP round failed", exc_info=True)
                self._sweep()
                wait = ROUND - (time.monotonic() - start)
                end = time.monotonic() + max(0.0, wait)
                while time.monotonic() < min(end, self._wanted_until):
                    time.sleep(0.5)
        finally:
            self._stop_fcast()

    def handle_ssdp(
        self, messages: list[ssdp.Message], describe: Callable[[str], str] | None = None
    ) -> None:
        """Lists the renderers among `messages`, reading each new one's
        description once (`describe` fetches it; tests replace it)."""
        for msg in messages:
            if not ssdp.is_renderer(msg) or not msg.udn:
                continue
            device_id = f"dlna:{msg.udn}"
            if not msg.alive:
                self.remove(device_id)
                continue
            if not msg.location or not ssdp.valid_location(msg.location, msg.sender):
                continue
            with self.lock:
                known = self._devices.get(device_id)
                if known is not None and known.location == msg.location:
                    self._seen[device_id] = time.monotonic()
                    continue
                if time.monotonic() < self._failed.get(device_id, 0):
                    continue
            device = self._describe(msg.udn, msg.location, describe)
            if device is None:
                with self.lock:
                    self._failed[device_id] = time.monotonic() + RETRY
                continue
            self.upsert(device)

    @staticmethod
    def _describe(
        udn: str, location: str, describe: Callable[[str], str] | None
    ) -> Device | None:
        from urllib.parse import urlsplit

        try:
            if describe is None:
                import httpx

                resp = httpx.get(location, timeout=5.0)
                if resp.status_code >= 300:
                    return None
                text = resp.text
            else:
                text = describe(location)
        except Exception:
            return None
        renderer = parse_description(text, location, udn)
        if renderer is None:
            return None
        url = urlsplit(location)
        return Device(
            f"dlna:{udn}",
            renderer.name,
            renderer.model,
            url.hostname or "",
            url.port or 80,
            "dlna",
            location,
        )

    def _sweep(self) -> None:
        now = time.monotonic()
        with self.lock:
            stale = [
                i
                for i, d in self._devices.items()
                if d.kind == "dlna" and now - self._seen.get(i, now) > STALE
            ]
        for device_id in stale:
            if self.session is None or self.session.device.id != device_id:
                self.remove(device_id)

    def _start_fcast(self) -> None:
        try:
            from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
        except ImportError:
            return
        controller = self

        def changed(
            zeroconf: Any, service_type: str, name: str, state_change: Any
        ) -> None:
            if state_change is ServiceStateChange.Removed:
                instance = name.removesuffix("." + service_type).rstrip(".")
                controller.remove(f"fcast:{instance}")
                return
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
            if info is None:
                return
            hosts = [a for a in info.parsed_addresses() if ":" not in a]
            device = device_from_service(
                name, hosts[0] if hosts else "", info.port or 46899
            )
            if device is not None:
                controller.upsert(device)

        try:
            zc = Zeroconf()
            browser = ServiceBrowser(zc, FCAST_SERVICE, handlers=[changed])
            self._zeroconf = (zc, browser)
        except Exception:
            logger.debug("[cast] FCast discovery failed", exc_info=True)

    def _stop_fcast(self) -> None:
        held, self._zeroconf = self._zeroconf, None
        if held:
            zc, browser = held
            try:
                browser.cancel()
                zc.close()
            except Exception:
                pass

    # ── the session ─────────────────────────────────────────────────────

    def _emit(self, snapshot: dict) -> None:
        try:
            self.push("app_cast_status", snapshot)
        except Exception:
            logger.debug("[cast] could not push status", exc_info=True)

    def connect(self, device_id: str) -> dict:
        with self.lock:
            device = self._devices.get(device_id)
        if device is None:
            raise RuntimeError("That device is no longer on the network")
        self.disconnect()
        status = Status()
        session_type = DlnaSession if device.kind == "dlna" else FcastSession
        session = session_type(device, status, self._emit)
        session.connect()
        with self.lock:
            self.session, self.status = session, status
        return status.snapshot()

    def _session(self) -> DlnaSession | FcastSession:
        if self.session is None or not self.status.connected:
            raise RuntimeError("No cast device is connected")
        return self.session

    def load(
        self,
        path: str,
        meta: dict | None = None,
        start: float = 0.0,
        autoplay: bool = True,
    ) -> dict:
        """Publishes `path` for the receiver and loads it there, then seeks
        to `start` and plays when `autoplay`."""
        session = self._session()
        file = Path(path)
        if not self.is_allowed(file) or not file.is_file():
            raise RuntimeError("This file cannot be cast")
        host = ssdp.local_address_for(session.device.host, session.device.port or 9)
        server = get_cast_media_server(self.is_allowed, host)
        meta = meta or {}
        self._token += 1
        item = {
            "url": server.url_for(file),
            # Receivers know the bare type, not its codec parameter.
            "mime": content_type_for(file).split(";", 1)[0].strip(),
            "token": self._token,
            "title": str(meta.get("title") or file.stem),
            "artist": str(meta.get("artist") or ""),
            "album": str(meta.get("album") or ""),
            "duration": int(float(meta.get("duration") or 0) * 1000) or None,
        }
        session.load(item)
        if start and start > 0:
            session.seek(float(start))
        if autoplay:
            session.play()
        return {**self.status.snapshot(), "token": self._token}

    def command(self, name: str, value: Any = None) -> dict:
        session = self._session()
        if name == "play":
            session.play()
        elif name == "pause":
            session.pause()
        elif name == "stop":
            session.stop()
        elif name == "seek":
            session.seek(float(value or 0))
        elif name == "volume":
            session.set_volume(float(value if value is not None else 1))
        else:
            raise RuntimeError(f"Unknown cast command: {name}")
        return self.status.snapshot()

    def disconnect(self) -> None:
        with self.lock:
            session, self.session = self.session, None
        if session is not None:
            try:
                session.disconnect()
            except Exception:
                logger.debug("[cast] disconnect failed", exc_info=True)
            self._emit(self.status.snapshot())


_controller: CastController | None = None
_controller_lock = threading.Lock()


def get_cast_controller(
    is_allowed: Callable[[Path], bool], push: Push
) -> CastController:
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = CastController(is_allowed, push)
        else:
            _controller.push = push
        return _controller
