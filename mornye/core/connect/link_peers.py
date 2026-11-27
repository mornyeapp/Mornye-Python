"""core/connect/link_peers.py — other computers running Mornye, over Link.

This computer pairs with another one the way the phone app does (its PIN in
Settings → Mornye Link there), then stays connected to it as a Link client:

- the other computer is a Connect device here (``mornye:<serverId>``, kind
  ``mornye``): "Play on" hands it the queue (``handoff/play``, after sending
  the songs it lacks), and every command reaches it (``command``);
- this computer is a device there in turn (it said ``kind: mornye`` in its
  ``hello``), so the other one can hand playback back: those arrive as
  ``command`` frames and play here.

The other computer's certificate is pinned at pairing: its PEM is the only
trust anchor of every later connection, so the key is never sent to anyone
else. ``computers.json`` (next to ``devices.json``, private) keeps the
paired computers and their keys.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import logging
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .. import link
from .model import CommandError, PlayerState, TrackRef, now_ms, state_changed

logger = logging.getLogger(__name__)

PEERS_FILE = "computers.json"
PEER_PREFIX = "mornye:"
SERVICE_TYPE = "_mornye._tcp.local."
#: Songs sent along with a hand-off: the one playing and the next ones.
UPLOAD_AHEAD = 10
#: A state report waits this long for more changes; while something plays
#: it is sent again this often (as the phone app does).
REPORT_DEBOUNCE_S = 0.25
REPORT_HEARTBEAT_S = 15.0
RETRY_MIN_S = 3.0
RETRY_MAX_S = 60.0
#: Kinds that play on this computer (not a phone, not another computer).
LOCAL_KINDS = ("window", "browser", "server", "cast")


def peer_device_id(server_id: str) -> str:
    return PEER_PREFIX + server_id


class PeerError(Exception):
    """Pairing or reaching another computer failed; the message says why."""


class _Unauthorized(Exception):
    """The other computer no longer knows this one's key (unpaired there)."""


@dataclass
class Peer:
    serverId: str
    name: str
    host: str
    port: int
    fingerprint: str
    apiKey: str
    cert: str
    platform: str = ""
    pairedMs: int = 0

    def public(self, state: str) -> dict[str, Any]:
        return {
            "serverId": self.serverId,
            "name": self.name,
            "address": f"{self.host}:{self.port}",
            "platform": self.platform,
            "pairedMs": self.pairedMs,
            "shortFingerprint": f"{self.fingerprint[:4]} {self.fingerprint[4:8]}",
            "state": state,
        }


# ── TLS ──────────────────────────────────────────────────────────────────


def _fetch_certificate(host: str, port: int) -> tuple[str, str]:
    """(PEM, fingerprint) of the certificate ``host:port`` presents."""
    try:
        pem = ssl.get_server_certificate((host, port), timeout=10)
    except (OSError, ssl.SSLError) as exc:
        raise PeerError(f"Could not reach {host}:{port}") from exc
    der = ssl.PEM_cert_to_DER_cert(pem)
    return pem, hashlib.sha256(der).hexdigest()


def pinned_context(pem: str) -> ssl.SSLContext:
    """TLS that trusts this one certificate and nothing else."""
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False
    return ctx


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


# ── discovery and pairing ────────────────────────────────────────────────


def discover(timeout_s: float = 2.5, exclude: str = "") -> list[dict[str, Any]]:
    """The computers announcing Mornye Link on this network (mDNS)."""
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        return []
    found: dict[str, dict[str, Any]] = {}
    zc = Zeroconf()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info is None:
            return
        props = {
            (k.decode() if isinstance(k, bytes) else str(k)): (
                v.decode() if isinstance(v, bytes) else str(v or "")
            )
            for k, v in (info.properties or {}).items()
        }
        server_id = props.get("id", "")
        addresses = info.parsed_addresses()
        if not server_id or server_id == exclude or not addresses:
            return
        found[server_id] = {
            "serverId": server_id,
            "name": name.removesuffix("." + service_type),
            "host": addresses[0],
            "port": int(info.port or 0),
            "fingerprint": props.get("fp", ""),
            "platform": props.get("pf", ""),
        }

    try:
        ServiceBrowser(zc, SERVICE_TYPE, handlers=[on_change])
        time.sleep(timeout_s)
    finally:
        zc.close()
    return sorted(found.values(), key=lambda p: p["name"].casefold())


def pair(
    host: str,
    port: int,
    pin: str,
    *,
    device_id: str,
    name: str,
    platform: str,
    expected_fingerprint: str = "",
) -> Peer:
    """Pairs with the computer at ``host:port`` showing ``pin``."""
    import httpx

    pin = "".join(c for c in str(pin or "") if c.isdigit())
    if len(pin) != 6:
        raise PeerError("Type the 6-digit PIN shown on the other computer.")
    pem, fingerprint = _fetch_certificate(host, port)
    if expected_fingerprint and expected_fingerprint.lower() != fingerprint:
        raise PeerError("That computer presented another certificate.")
    base = f"https://{_url_host(host)}:{port}/mornye/v1"
    try:
        with httpx.Client(verify=pinned_context(pem), timeout=15) as http:
            info = http.get(f"{base}/info").json()
            if info.get("serverId") == device_id:
                raise PeerError("That is this computer.")
            if info.get("fingerprint") != fingerprint:
                raise PeerError("That computer presented another certificate.")
            answer = http.post(
                f"{base}/pair",
                json={
                    "deviceId": device_id,
                    "name": name,
                    "platform": platform,
                    "proof": link.pairing_proof(pin, fingerprint, device_id),
                },
            )
    except httpx.HTTPError as exc:
        raise PeerError(f"Could not reach {host}:{port}") from exc
    if answer.status_code != 200:
        try:
            code = str(answer.json().get("error") or "")
        except ValueError:
            code = ""
        raise PeerError(
            {
                "pairing_closed": "Show a PIN on the other computer first.",
                "bad_proof": "Wrong PIN.",
                "rate_limited": "Too many tries: wait a moment.",
            }.get(code, f"Pairing failed ({code or answer.status_code}).")
        )
    body = answer.json()
    return Peer(
        serverId=str(body["serverId"]),
        name=str(body.get("name") or info.get("name") or host),
        host=host,
        port=int(port),
        fingerprint=fingerprint,
        apiKey=str(body["apiKey"]),
        cert=pem,
        platform=str(info.get("platform") or ""),
        pairedMs=now_ms(),
    )


def request_pair(
    host: str,
    port: int,
    *,
    device_id: str,
    name: str,
    platform: str,
    expected_fingerprint: str = "",
    give_up: threading.Event | None = None,
    deadline_s: float = 150.0,
) -> Peer:
    """Pairs without a PIN (``pair/request``): at once when that computer
    trusts this network, else once someone allows it there. Blocks until
    then (at most ``deadline_s``, or until ``give_up`` is set)."""
    import httpx

    pem, fingerprint = _fetch_certificate(host, port)
    if expected_fingerprint and expected_fingerprint.lower() != fingerprint:
        raise PeerError("That computer presented another certificate.")
    base = f"https://{_url_host(host)}:{port}/mornye/v1"
    hello = {"deviceId": device_id, "name": name, "platform": platform}
    end = time.monotonic() + deadline_s
    try:
        with httpx.Client(verify=pinned_context(pem), timeout=40) as http:
            info = http.get(f"{base}/info").json()
            if info.get("serverId") == device_id:
                raise PeerError("That is this computer.")
            if info.get("fingerprint") != fingerprint:
                raise PeerError("That computer presented another certificate.")
            if "pair.request" not in (info.get("features") or []):
                raise PeerError("That computer needs a PIN: update Mornye there.")
            answer = http.post(f"{base}/pair/request", json=hello)
            if answer.status_code == 429:
                raise PeerError("That computer has too many requests waiting.")
            body = answer.json()
            request_id = str(body.get("requestId") or "")
            while body.get("status") == "pending":
                if time.monotonic() > end or (give_up and give_up.is_set()):
                    raise PeerError("No answer there.")
                body = http.post(
                    f"{base}/pair/wait",
                    json={"requestId": request_id, "deviceId": device_id},
                ).json()
    except httpx.HTTPError as exc:
        raise PeerError(f"Could not reach {host}:{port}") from exc
    status = body.get("status")
    if status == "denied":
        raise PeerError("Refused there.")
    if status != "approved":
        raise PeerError("No answer there.")
    return Peer(
        serverId=str(body["serverId"]),
        name=str(body.get("name") or info.get("name") or host),
        host=host,
        port=int(port),
        fingerprint=fingerprint,
        apiKey=str(body["apiKey"]),
        cert=pem,
        platform=str(info.get("platform") or ""),
        pairedMs=now_ms(),
    )


# ── one connected computer ───────────────────────────────────────────────


@dataclass
class Local:
    """What a connection to another computer needs from this one."""

    device_id: str
    name: str
    platform: str
    app_version: str
    hub: Callable[[], Any]
    catalog: Callable[[], Any]


def local_target(hub: Any) -> str | None:
    """What plays on this computer: the active device when it is one of
    this computer's own, else the desktop window, else any page."""
    snap = hub.snapshot()
    devices = [d for d in snap["devices"] if d["kind"] in LOCAL_KINDS and d["canPlay"]]
    active = next((d for d in devices if d["id"] == snap["activeId"]), None)
    if active is not None:
        return str(active["id"])
    ranked = sorted(devices, key=lambda d: (d["kind"] != "window", "state" not in d))
    return str(ranked[0]["id"]) if ranked else None


@dataclass
class PeerLink:
    """The connection to one paired computer, kept open (reconnecting with
    a growing pause) until stopped or unpaired there."""

    peer: Peer
    local: Local
    on_unauthorized: Callable[[str], None] = lambda server_id: None
    state: str = "connecting"
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _ws: Any = None
    _work: concurrent.futures.ThreadPoolExecutor | None = None
    _unlisten: Callable[[], None] | None = None
    _report_timer: threading.Timer | None = None
    _last_report: PlayerState | None = None
    _report_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def device_id(self) -> str:
        return peer_device_id(self.peer.serverId)

    @property
    def base(self) -> str:
        return f"https://{_url_host(self.peer.host)}:{self.peer.port}/mornye/v1"

    # ── lifecycle ──
    def start(self) -> None:
        # One worker: commands reach the other computer in the order given.
        self._work = concurrent.futures.ThreadPoolExecutor(
            1, thread_name_prefix="mornye-peer-op"
        )
        self._thread = threading.Thread(
            target=self._run, name="mornye-peer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()
        if self._work is not None:
            self._work.shutdown(wait=False, cancel_futures=True)
        self._drop_device()

    def _run(self) -> None:
        delay = RETRY_MIN_S
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._session()
            except _Unauthorized:
                self.state = "unauthorized"
                self._drop_device()
                self.on_unauthorized(self.peer.serverId)
                return
            except Exception as exc:
                logger.debug("[link] %s: %s", self.peer.name, exc)
            self._drop_device()
            if self._stop.is_set():
                return
            self.state = "offline"
            if time.monotonic() - started > RETRY_MAX_S:
                delay = RETRY_MIN_S
            self._stop.wait(delay)
            delay = min(delay * 2, RETRY_MAX_S)

    def _session(self) -> None:
        from websockets.exceptions import ConnectionClosed, InvalidStatus
        from websockets.sync.client import connect

        url = f"wss://{_url_host(self.peer.host)}:{self.peer.port}/mornye/v1/ws"
        try:
            ws = connect(
                url,
                ssl=pinned_context(self.peer.cert),
                additional_headers={"Authorization": f"Bearer {self.peer.apiKey}"},
                open_timeout=10,
                max_size=8 << 20,
            )
        except InvalidStatus as exc:
            if exc.response.status_code == 401:
                raise _Unauthorized from None
            raise
        self._ws = ws
        try:
            ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "deviceId": self.local.device_id,
                        "name": self.local.name,
                        "platform": self.local.platform,
                        "appVersion": self.local.app_version,
                        "kind": "mornye",
                    }
                )
            )
            welcome = json.loads(ws.recv(timeout=15))
            if welcome.get("type") != "welcome":
                raise PeerError("no welcome")
            if welcome.get("name"):
                self.peer.name = str(welcome["name"])
            self.state = "connected"
            self._register()
            self._start_reporting()
            for text in ws:
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._on_frame(message)
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 4401:
                raise _Unauthorized from None
            raise
        finally:
            self._stop_reporting()
            self._ws = None
            with contextlib.suppress(Exception):
                ws.close()

    # ── the hub ──
    def _register(self) -> None:
        from .link_bridge import phone_device_id

        hub = self.local.hub()
        # Paired both ways: the other computer may be connected here as a
        # client too; one entry for it is enough.
        hub.unregister(phone_device_id(self.peer.serverId))
        hub.register(self.device_id, self.peer.name, "mornye", sender=self._sender)

    # ── what plays here, told to the other computer ──
    def _start_reporting(self) -> None:
        """Sends what plays on this computer as its ``player.state``, as a
        phone does: the other one lists it as playing, and a page opened
        there while nothing else plays shows it."""
        self._last_report = None
        self._unlisten = self.local.hub().add_listener(
            lambda event, payload: self._report_soon()
        )
        self._report_soon()

    def _stop_reporting(self) -> None:
        with self._report_lock:
            if self._unlisten is not None:
                self._unlisten()
                self._unlisten = None
            if self._report_timer is not None:
                self._report_timer.cancel()
                self._report_timer = None

    def _report_soon(
        self, delay: float = REPORT_DEBOUNCE_S, force: bool = False
    ) -> None:
        with self._report_lock:
            if self._unlisten is None:
                return
            if self._report_timer is not None:
                self._report_timer.cancel()
            self._report_timer = threading.Timer(delay, self._report, (force,))
            self._report_timer.daemon = True
            self._report_timer.start()

    def _report(self, force: bool = False) -> None:
        ws = self._ws
        if ws is None:
            return
        hub = self.local.hub()
        target = local_target(hub)
        device = hub.device(target) if target else None
        state = (
            device.state.at()
            if device is not None and device.state is not None
            else PlayerState(updatedMs=now_ms())
        )
        if state.playing and target != hub.active_id:
            # What plays is elsewhere (the other computer, a phone): nothing
            # plays here, whatever a page here last said. Reported playing,
            # it would take the controls away from the one that plays as
            # soon as that one paused.
            state = replace(state, playing=False)
        if force or state_changed(self._last_report, state):
            self._last_report = state
            try:
                ws.send(json.dumps({"type": "player.state", "state": state.to_json()}))
            except Exception:
                return
        if state.playing:
            self._report_soon(REPORT_HEARTBEAT_S, force=True)

    def _drop_device(self) -> None:
        with contextlib.suppress(Exception):
            self.local.hub().unregister(self.device_id)

    def _on_frame(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "player.state":
            with contextlib.suppress(CommandError):
                self.local.hub().report_state(
                    self.device_id, message.get("state"), claim=False
                )
        elif kind == "command":
            args = message.get("args")
            self.run_here(
                str(message.get("action") or ""), args if isinstance(args, dict) else {}
            )

    def run_here(self, action: str, args: dict[str, Any]) -> None:
        """A command the other computer sent this one: it plays here."""
        hub = self.local.hub()
        target = local_target(hub)
        if target is None:
            return
        try:
            if action == "play_queue":
                hub.transfer(target, handoff=args)
            else:
                hub.command(action, args, target=target)
        except CommandError as exc:
            logger.info("[link] %s → here: %s", self.peer.name, exc.message)

    # ── reaching the other computer ──
    def _sender(self, action: str, args: dict[str, Any]) -> None:
        if self._work is not None:
            self._work.submit(self._send_safely, action, dict(args))

    def _send_safely(self, action: str, args: dict[str, Any]) -> None:
        try:
            if action == "play_queue":
                self.hand_off(args)
            else:
                self.op("command", {"action": action, "args": args})
        except Exception as exc:
            logger.warning("[link] could not reach %s: %s", self.peer.name, exc)

    def _client(self, **extra: Any) -> Any:
        import httpx

        return httpx.Client(
            verify=pinned_context(self.peer.cert),
            headers={"Authorization": f"Bearer {self.peer.apiKey}"},
            **{"timeout": 15, **extra},
        )

    def op(self, op: str, body: dict[str, Any] | None = None) -> Any:
        with self._client() as http:
            answer = http.post(f"{self.base}/{op}", json=body or {})
        if answer.status_code == 401:
            raise _Unauthorized
        try:
            out = answer.json() if answer.content else {}
        except ValueError:
            out = {}
        if answer.status_code >= 400:
            raise CommandError(
                str(out.get("error") or "unavailable"), str(out.get("message") or "")
            )
        return out

    def hand_off(self, args: dict[str, Any]) -> None:
        """``play_queue`` for the other computer: the songs it lacks near
        the current one are sent first, then the queue plays there."""
        queue = list(args.get("queue") or [])
        raw_index = args.get("index")
        index = raw_index if isinstance(raw_index, int) else 0
        check = self.op("handoff/play", {**args, "check": True})
        missing = set(check.get("missing") or [])
        for i in range(index, min(len(queue), index + UPLOAD_AHEAD + 1)):
            if i in missing:
                file_id = self.upload(queue[i])
                if file_id:
                    queue[i] = {**queue[i], "fileId": file_id}
        self.op("handoff/play", {**args, "queue": queue})

    def upload(self, raw: Any) -> str:
        """Sends the file of a queue entry this library has; its file id,
        or "" when there is nothing to send."""
        import httpx

        from .link_bridge import AUDIO_EXTENSIONS, MAX_UPLOAD

        ref = TrackRef.from_json(raw)
        catalog = self.local.catalog()
        song = catalog.song_for_ref(ref) if ref and catalog is not None else None
        if song is None:
            return ""
        path = Path(song.path)
        ext = path.suffix.lstrip(".").lower()
        try:
            stat = path.stat()
        except OSError:
            return ""
        if ext not in AUDIO_EXTENSIONS or stat.st_size > MAX_UPLOAD:
            return ""
        file_id = hashlib.md5(
            f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        with (
            path.open("rb") as handle,
            self._client(timeout=httpx.Timeout(30, write=600)) as http,
        ):
            answer = http.put(f"{self.base}/files/{file_id}.{ext}", content=handle)
        return file_id if answer.status_code == 200 else ""


# ── every paired computer ────────────────────────────────────────────────


class LinkPeers:
    """The paired computers and their connections (one per process)."""

    def __init__(self, directory: Path, local: Callable[[], Local]) -> None:
        self.dir = directory
        self._local = local
        self._lock = threading.Lock()
        self._links: dict[str, PeerLink] = {}
        self._unauthorized: set[str] = set()
        #: Connections without a PIN waiting for an answer there, by address.
        self._asking: dict[str, dict[str, Any]] = {}
        self._peers: dict[str, Peer] = self._load()

    # ── the file ──
    def _load(self) -> dict[str, Peer]:
        try:
            raw = json.loads((self.dir / PEERS_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        peers: dict[str, Peer] = {}
        names = set(Peer.__dataclass_fields__)
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                peer = Peer(**{k: v for k, v in item.items() if k in names})
            except TypeError:
                continue
            peers[peer.serverId] = peer
        return peers

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps([asdict(p) for p in self._peers.values()], indent=2)
        link.write_private(self.dir / PEERS_FILE, data.encode("utf-8"))

    # ── what the settings page calls ──
    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for peer in self._peers.values():
                if peer.serverId in self._unauthorized:
                    state = "unauthorized"
                else:
                    running = self._links.get(peer.serverId)
                    state = running.state if running else "offline"
                out.append(peer.public(state))
            for key, asking in self._asking.items():
                out.append(
                    {
                        "serverId": key,
                        "name": asking["name"],
                        "address": key.removeprefix("request:"),
                        "platform": "",
                        "pairedMs": 0,
                        "shortFingerprint": "",
                        "state": asking["state"],
                        "message": asking.get("message", ""),
                    }
                )
        return sorted(out, key=lambda p: p["name"].casefold())

    def ask(
        self, host: str, port: int, fingerprint: str = "", name: str = ""
    ) -> dict[str, Any]:
        """Asks the computer at ``host:port`` to connect without a PIN, in
        the background: ``status()`` lists it as ``waiting`` until someone
        answers there, then it is paired (or ``refused``/``failed``)."""
        key = f"request:{_url_host(host)}:{port}"
        give_up = threading.Event()
        with self._lock:
            current = self._asking.get(key)
            if current is not None and current["state"] == "waiting":
                return dict(current)
            self._asking[key] = {
                "name": name or host,
                "state": "waiting",
                "give_up": give_up,
            }

        def run() -> None:
            local = self._local()
            try:
                peer = request_pair(
                    host,
                    port,
                    device_id=local.device_id,
                    name=local.name,
                    platform=local.platform,
                    expected_fingerprint=fingerprint,
                    give_up=give_up,
                )
            except PeerError as exc:
                self._asked(
                    key, "refused" if "Refused" in str(exc) else "failed", str(exc)
                )
                return
            except Exception as exc:
                logger.warning("[link] asking %s failed: %s", host, exc)
                self._asked(key, "failed", "Connecting failed.")
                return
            with self._lock:
                self._asking.pop(key, None)
            self._paired(peer)

        threading.Thread(target=run, name="mornye-peer-ask", daemon=True).start()
        return {"name": name or host, "state": "waiting"}

    def _asked(self, key: str, state: str, message: str) -> None:
        with self._lock:
            if key in self._asking:
                self._asking[key].update(state=state, message=message)

    def discover(self) -> list[dict[str, Any]]:
        own = self._local().device_id
        with self._lock:
            paired = set(self._peers)
        return [
            {**found, "paired": found["serverId"] in paired}
            for found in discover(exclude=own)
        ]

    def pair(
        self, host: str, port: int, pin: str, fingerprint: str = ""
    ) -> dict[str, Any]:
        local = self._local()
        peer = pair(
            host,
            port,
            pin,
            device_id=local.device_id,
            name=local.name,
            platform=local.platform,
            expected_fingerprint=fingerprint,
        )
        self._paired(peer)
        return peer.public("connecting")

    def _paired(self, peer: Peer) -> None:
        with self._lock:
            old = self._links.pop(peer.serverId, None)
            self._peers[peer.serverId] = peer
            self._unauthorized.discard(peer.serverId)
            self._save()
        if old is not None:
            old.stop()
        self._connect(peer)

    def remove(self, server_id: str) -> bool:
        with self._lock:
            asking = self._asking.pop(server_id, None)
            if asking is not None:
                asking["give_up"].set()
                return True
            peer = self._peers.pop(server_id, None)
            running = self._links.pop(server_id, None)
            self._unauthorized.discard(server_id)
            if peer is not None:
                self._save()
        if running is not None:
            running.stop()
        return peer is not None

    # ── connections ──
    def start(self) -> None:
        """Connects to every paired computer (again after a restart)."""
        with self._lock:
            peers = [
                p
                for p in self._peers.values()
                if p.serverId not in self._links
                and p.serverId not in self._unauthorized
            ]
        for peer in peers:
            self._connect(peer)

    def stop(self) -> None:
        with self._lock:
            running = list(self._links.values())
            self._links.clear()
        for item in running:
            item.stop()

    def connection(self, server_id: str) -> PeerLink | None:
        with self._lock:
            return self._links.get(server_id)

    def _connect(self, peer: Peer) -> None:
        running = PeerLink(peer, self._local(), on_unauthorized=self._lost_key)
        with self._lock:
            if peer.serverId in self._links:
                return
            self._links[peer.serverId] = running
        running.start()

    def _lost_key(self, server_id: str) -> None:
        with self._lock:
            self._links.pop(server_id, None)
            self._unauthorized.add(server_id)


_peers: LinkPeers | None = None
_peers_lock = threading.Lock()


def get_link_peers() -> LinkPeers:
    """The process's paired computers, kept with the Link server's files."""
    global _peers
    with _peers_lock:
        if _peers is None:
            from ...link_server import get_link_server
            from .hub import get_connect_hub
            from .resolver import catalog

            server = get_link_server()

            def local() -> Local:
                return Local(
                    device_id=server.store.server_id,
                    name=server.name,
                    platform=server.platform,
                    app_version=server.app_version,
                    hub=lambda: get_connect_hub(server.connect_owner()),
                    catalog=catalog,
                )

            _peers = LinkPeers(server.dir, local)
        return _peers
