"""link_server.py — the Mornye Link server a phone pairs with.

The phone app's Link protocol (version 1), as the Rust desktop serves it
(`rust_backend/crates/link`):

- one TLS port on every interface — the persisted one, else the first free
  one of 47710..47729, else any — with the pinned certificate from
  core/link.py;
- ``GET /mornye/v1/info`` and ``POST /mornye/v1/pair``, unauthenticated;
- ``GET /mornye/v1/ws``, the push socket: ``hello`` → ``welcome``;
- ``/rest/*``, the OpenSubsonic API of webapi/subsonic.py, opened by the
  paired devices' keys instead of MORNYE_SUBSONIC_*;
- the desktop player's ops — ``state``, ``command``, ``handoff/*``,
  ``queue/add``, ``player/details``, ``resolve`` and ``PUT files/<id>.<ext>``
  — and ``player.state`` / ``command`` frames on the socket, answered by
  Mornye Connect (core/connect/link_bridge.py): the phone controls whatever
  plays on this account, hands its queue over, and shows up in "Play on";
- any other ``/mornye/v1/<op>``: ``404 not_found``. ``features`` in ``info``
  and ``welcome`` stays empty (no downloads, open or library jobs here); the
  player ops need no flag.
- ``_mornye._tcp`` over mDNS (``zeroconf``) with TXT ``v``, ``id``, ``fp``
  and ``pf``, so the phone finds the computer on the same Wi-Fi; where
  multicast is blocked the phone adds it by its address.

One server per process (`get_link_server()`): the desktop window and every
--web account share it.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import platform as _platform
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .core import link

logger = logging.getLogger(__name__)

PORT_RANGE = range(47710, 47730)
SERVICE_TYPE = "_mornye._tcp.local."
MAX_BODY = 8 << 20
CLOSE_UNAUTHORIZED = 4401
CLOSE_REPLACED = 4409


def host_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def computer_name() -> str:
    """The name the phone lists: macOS's Computer Name (as AirDrop shows
    it), else the host name. A host name that is only an address (a Mac
    whose DHCP lease named it) says nothing, so it gives way to a generic
    one."""
    if sys.platform == "darwin":
        import subprocess

        with contextlib.suppress(OSError, subprocess.SubprocessError):
            done = subprocess.run(
                ["scutil", "--get", "ComputerName"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
    name = _platform.node() or socket.gethostname() or ""
    for suffix in (".local", ".lan", ".home"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("-", " ").strip()
    if not name or all(c.isdigit() or c in ".:" for c in name):
        return f"Mornye on {host_platform().capitalize().replace('Macos', 'macOS')}"
    return name


def _app_version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def _listen(family: int, address: str, port: int, dual_stack: bool) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if dual_stack:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        # Windows lets SO_REUSEADDR take over a busy port; elsewhere it only
        # allows rebinding while old connections linger in TIME_WAIT.
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def _bind(port: int, only: str | None) -> socket.socket:
    if only:
        family = socket.AF_INET6 if ":" in only else socket.AF_INET
        return _listen(family, only, port, False)
    try:
        return _listen(socket.AF_INET6, "::", port, True)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise
    # No IPv6 here: every IPv4 interface.
    return _listen(socket.AF_INET, "0.0.0.0", port, False)


def bind_listener(persisted: int, only: str | None = None) -> socket.socket:
    """The port policy: persisted, 47710..47729, then any free port."""
    candidates = list(dict.fromkeys(p for p in (persisted, *PORT_RANGE) if p)) + [0]
    last: OSError | None = None
    for port in candidates:
        try:
            return _bind(port, only)
        except OSError as exc:
            last = exc
    raise OSError(f"Mornye Link could not listen: {last}")


def lan_addresses() -> list[str]:
    """IPv4 addresses a phone on the same network can reach."""
    found: list[str] = []
    with contextlib.suppress(OSError):
        # No packet is sent: connect() on UDP only picks the outgoing route.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    with contextlib.suppress(OSError):
        found.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    return [
        ip
        for ip in dict.fromkeys(found)
        if not ip.startswith(("127.", "169.254.", "0."))
    ]


async def _close_quietly(ws: Any, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code, reason=reason)


class LinkServer:
    """The state the routes share, and the thread that serves them."""

    def __init__(
        self,
        directory: Path | None = None,
        roots: Callable[[], list[str]] | None = None,
        *,
        ttl_s: float = 300.0,
        pair_interval_s: float = 1.0,
        bind: str | None = None,
        advertise: bool = True,
    ) -> None:
        self.dir = directory or link.link_dir()
        self.store = link.LinkStore(self.dir)
        self.pairing = link.PairingWindow(ttl_s, pair_interval_s)
        self.roots: Callable[[], list[str]] = roots or (lambda: [])
        self.bind = bind
        self.advertise = advertise
        self.name = computer_name()
        self.platform = host_platform()
        self.app_version = _app_version()
        self.identity: link.Identity | None = None
        self.port = 0
        self.error = ""
        self.mdns_error = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        # device id → (websocket, client info from `hello`)
        self.clients: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._clients_lock = threading.Lock()
        self._life = threading.Lock()
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._zeroconf: Any = None
        self._started = threading.Event()
        #: The account whose Connect devices a phone sees: the desktop app's
        #: (None); webapp.py points it at an admin in multi-user mode.
        self.connect_owner: Callable[[], str | None] = lambda: None
        from .core.connect.link_bridge import LinkConnect

        self.connect = LinkConnect(
            self._send_frame,
            self._connected_devices,
            received_dir=lambda: self.dir / "received",
            catalog=self._connect_catalog,
            owner=lambda: self.connect_owner(),
            loop=lambda: self.loop,
        )

    # -- Mornye Connect -------------------------------------------------------

    def _connect_catalog(self) -> Any:
        from .core.connect.resolver import catalog

        try:
            return catalog(self.store.server_id)
        except Exception:
            logger.warning("Link: the library index is not ready", exc_info=True)
            return None

    def _connected_devices(self) -> list[str]:
        with self._clients_lock:
            return list(self.clients)

    def _send_frame(self, device_id: str, message: dict[str, Any]) -> None:
        """Writes a frame to a phone's socket, from any thread."""
        import json

        with self._clients_lock:
            entry = self.clients.get(device_id)
        loop = self.loop
        if entry is None or loop is None or not loop.is_running():
            return

        async def send() -> None:
            with contextlib.suppress(Exception):
                await entry[0].send_text(json.dumps(message))

        asyncio.run_coroutine_threadsafe(send(), loop)

    # -- what the routes and the settings page read -----------------------

    @property
    def fingerprint(self) -> str:
        return self.identity.fingerprint if self.identity else ""

    def announce(self, body: dict[str, Any]) -> dict[str, Any]:
        body["platform"] = self.platform
        # None of the phone's LinkHostFeatures (downloads, open, library.jobs);
        # the player ops (Mornye Connect) need no flag.
        body["features"] = []
        return body

    def info(self) -> dict[str, Any]:
        return self.announce(
            {
                "serverId": self.store.server_id,
                "name": self.name,
                "protocol": link.PROTOCOL,
                "appVersion": self.app_version,
                "fingerprint": self.fingerprint,
                "pairing": self.pairing.is_open(),
            }
        )

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self.port)

    def status(self) -> dict[str, Any]:
        with self._clients_lock:
            connected = set(self.clients)
        fp = self.fingerprint
        running = self.running()
        return {
            "enabled": self.store.enabled,
            "running": running,
            "error": self.error,
            "name": self.name,
            "port": self.port,
            "addresses": (
                [f"{ip}:{self.port}" for ip in lan_addresses()] if running else []
            ),
            "fingerprint": fp,
            # As the phone shows it next to the PIN.
            "shortFingerprint": f"{fp[:4]} {fp[4:8]}" if fp else "",
            "discovery": bool(self._zeroconf),
            "discoveryError": self.mdns_error,
            "pairing": self.pairing.current() if running else None,
            "pairingEnded": self.pairing.last_ended,
            "devices": [
                d.public(d.deviceId in connected) for d in self.store.devices()
            ],
        }

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> dict[str, Any]:
        with self._life:
            if self.running():
                return self.status()
            self.error = ""
            try:
                self.identity = link.load_or_create_identity(self.dir)
                sock = bind_listener(self.store.port, self.bind)
            except Exception as exc:
                self.error = str(exc)
                logger.warning("[link] not started: %s", exc)
                return self.status()
            self.port = sock.getsockname()[1]
            self.store.set_server(self.port, self.name)
            self._started.clear()
            self._thread = threading.Thread(
                target=self._serve, args=(sock,), name="mornye-link", daemon=True
            )
            self._thread.start()
            self._started.wait(10)
            if not self.running():
                self.port = 0
                return self.status()
            if self.advertise:
                # mDNS probes the name for a couple of seconds first; the
                # settings page polls, so the switch need not wait for it.
                threading.Thread(
                    target=self._advertise, name="mornye-link-mdns", daemon=True
                ).start()
            logger.info("[link] listening on port %d", self.port)
            return self.status()

    def _serve(self, sock: socket.socket) -> None:
        import uvicorn

        from .webapi.link import build_link_app

        assert self.identity is not None
        from uvicorn.config import WS_PROTOCOLS

        # The sans-I/O backend (uvicorn >= 0.35) avoids websockets' legacy API.
        ws = "websockets-sansio" if "websockets-sansio" in WS_PROTOCOLS else "auto"
        config = uvicorn.Config(
            build_link_app(self),
            ws=ws,
            ssl_certfile=str(self.identity.cert_path),
            ssl_keyfile=str(self.identity.key_path),
            log_level="warning",
            access_log=False,
            # Protocol-level pings; a client silent past the timeout is dropped.
            ws_ping_interval=20.0,
            ws_ping_timeout=40.0,
            ws_max_size=MAX_BODY,
            timeout_keep_alive=30,
            lifespan="off",
        )
        server = uvicorn.Server(config)
        self._server = server
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)

        async def ready() -> None:
            # `serve()` sets `started` once the socket accepts connections.
            # uvicorn exposes it as a plain flag, with no event to await.
            while not server.started and not server.should_exit:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
            self._started.set()

        async def run() -> None:
            watcher = asyncio.ensure_future(ready())
            try:
                # Signal handlers are only installed from the main thread,
                # so this leaves the app's own Ctrl+C handling alone.
                await server.serve(sockets=[sock])
            finally:
                watcher.cancel()

        try:
            loop.run_until_complete(run())
        except BaseException as exc:
            self.error = str(exc) or type(exc).__name__
            logger.exception("[link] server stopped")
        finally:
            self._started.set()
            with contextlib.suppress(OSError):
                sock.close()
            self.loop = None
            loop.close()

    def stop(self) -> None:
        with self._life:
            self.pairing.end()
            self._unadvertise()
            if self._server is not None:
                self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(5)
            self._thread = None
            self._server = None
            self.port = 0
            with self._clients_lock:
                self.clients.clear()

    def set_enabled(self, on: bool) -> dict[str, Any]:
        self.store.set_enabled(bool(on))
        if on:
            return self.start()
        self.stop()
        return self.status()

    def begin_pairing(self) -> dict[str, Any]:
        if not self.running():
            self.start()
        if self.running():
            self.pairing.begin()
        return self.status()

    def end_pairing(self) -> dict[str, Any]:
        self.pairing.end()
        return self.status()

    def revoke(self, device_id: str) -> dict[str, Any]:
        if self.store.revoke(device_id):
            self.disconnect(device_id, CLOSE_UNAUTHORIZED, "revoked")
        return self.status()

    # -- WebSocket clients --------------------------------------------------

    def register_client(self, device_id: str, ws: Any, info: dict[str, Any]) -> Any:
        """Records the device's socket; returns the one it replaces."""
        with self._clients_lock:
            old = self.clients.get(device_id)
            self.clients[device_id] = (ws, info)
        return old[0] if old else None

    def unregister_client(self, device_id: str, ws: Any) -> None:
        with self._clients_lock:
            current = self.clients.get(device_id)
            if current and current[0] is ws:
                del self.clients[device_id]

    def disconnect(self, device_id: str, code: int, reason: str) -> None:
        with self._clients_lock:
            entry = self.clients.pop(device_id, None)
        loop = self.loop
        if entry and loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _close_quietly(entry[0], code, reason), loop
            )

    # -- mDNS ---------------------------------------------------------------

    def _advertise(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            self.mdns_error = "zeroconf is not installed"
            return
        try:
            short = "".join(c for c in self.store.server_id if c.isalnum())[:8]
            info = ServiceInfo(
                SERVICE_TYPE,
                f"{self.name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip) for ip in lan_addresses()],
                port=self.port,
                properties={
                    "v": "1",
                    "id": self.store.server_id,
                    "fp": self.fingerprint,
                    "pf": self.platform,
                },
                server=f"mornye-{short}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
            self._zeroconf = (zc, info)
            self.mdns_error = ""
            # Switched off while the name was being probed: take it back.
            if not self.running():
                self._unadvertise()
        except Exception as exc:
            self.mdns_error = f"mDNS advertising failed: {exc}"
            logger.warning("[link] %s", self.mdns_error)

    def _unadvertise(self) -> None:
        if not self._zeroconf:
            return
        zc, info = self._zeroconf
        self._zeroconf = None
        with contextlib.suppress(Exception):
            zc.unregister_service(info)
            zc.close()


_SERVER: LinkServer | None = None
_SERVER_LOCK = threading.Lock()


def get_link_server(roots: Callable[[], list[str]] | None = None) -> LinkServer:
    """The process's Link server; `roots` (the library folders) is taken
    from the first caller that gives one."""
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER is None:
            _SERVER = LinkServer(roots=roots)
        elif roots is not None:
            _SERVER.roots = roots
        return _SERVER


__all__ = ["LinkServer", "bind_listener", "get_link_server", "lan_addresses"]
