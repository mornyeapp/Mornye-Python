"""core/link.py — Mornye Link: pairing a phone with this computer.

The Mornye phone app pairs with a computer over the Link protocol (the Rust
desktop's `rust_backend/crates/link`, protocol 1). This module is the state
behind it; webapi/link.py is the HTTP side and link_server.py runs both.

- Identity: a self-signed ECDSA P-256 certificate for ``mornye.local``,
  created once under ``<data_dir>/link`` and reused. Phones pin its
  fingerprint, the lowercase hex SHA-256 of the DER certificate.
- Pairing: the computer shows a 6-digit PIN for five minutes. The phone
  proves it knows the PIN *and* saw this certificate:
  ``hex(HMAC-SHA256(key=PIN, msg="mornye-link-pair-v1:" + fp + ":" + id))``,
  so someone relaying the connection presents another certificate and
  fails. Five wrong proofs close the window; one request a second per
  address.
- Without a PIN (``PairRequests``): the phone asks (``pair/request``) and
  a person allows it on this computer, or, with "Trust this network" on
  (``trustNetwork`` in ``server.json``), a device on the local network is
  paired at once.
- Devices: every paired phone owns one API key, in ``devices.json``. It
  opens the Subsonic API (as ``apiKey``, as the password of a salted token,
  or as a plain/``enc:`` password, with any username) and the ``/mornye/v1``
  endpoints (``Authorization: Bearer``).

Files are replaced atomically and are private to the user where the OS
allows it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

PROTOCOL = 1
PROOF_CONTEXT = "mornye-link-pair-v1:"
CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
SERVER_FILE = "server.json"
DEVICES_FILE = "devices.json"
SUBJECT_NAME = "mornye.local"
VALIDITY_DAYS = 20 * 365 + 5
MAX_ATTEMPTS = 5
#: Devices asking to connect without a PIN that can wait at once.
MAX_PENDING_REQUESTS = 5
MAX_DEVICE_ID = 200
# `lastSeenMs` is written to disk at most this often per device.
_TOUCH_INTERVAL_MS = 60_000

# Why a pairing window ended.
PAIRED = "paired"
EXPIRED = "expired"
ATTEMPTS = "attempts"
CANCELLED = "cancelled"

T = TypeVar("T")


def now_ms() -> int:
    return int(time.time() * 1000)


def link_dir() -> Path:
    from .paths import data_dir

    return data_dir() / "link"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def write_private(path: Path, data: bytes) -> None:
    """Atomic write, readable by the user only (where chmod means anything)."""
    _private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    """The file's JSON, None when absent; an unreadable file is moved aside
    to ``<name>.bad`` and treated as absent."""
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("[link] %s is unreadable; starting it over", path.name)
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return None


def _write_json(path: Path, value: Any) -> None:
    write_private(path, json.dumps(value, indent=2).encode("utf-8"))


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    cert_path: Path
    key_path: Path
    fingerprint: str


def fingerprint_of(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _load_identity(directory: Path) -> Identity | None:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert_path, key_path = directory / CERT_FILE, directory / KEY_FILE
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return None
    # The pair must belong together, or TLS would fail on the first phone.
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    enc = serialization.Encoding.DER
    if cert.public_key().public_bytes(enc, pub) != key.public_key().public_bytes(
        enc, pub
    ):
        return None
    der = cert.public_bytes(serialization.Encoding.DER)
    return Identity(cert_path, key_path, fingerprint_of(der))


def load_or_create_identity(directory: Path) -> Identity:
    """The certificate in ``directory``, created when missing or unusable."""
    found = _load_identity(directory)
    if found:
        return found
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SUBJECT_NAME)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # A day of slack for phones whose clock runs behind.
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(SUBJECT_NAME)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    # Key first: a certificate without its key is never left behind.
    write_private(
        directory / KEY_FILE,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    write_private(directory / CERT_FILE, cert.public_bytes(serialization.Encoding.PEM))
    loaded = _load_identity(directory)
    if not loaded:
        raise RuntimeError("The new Mornye Link certificate could not be loaded")
    return loaded


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


@dataclass
class Device:
    deviceId: str
    name: str
    platform: str
    apiKey: str
    pairedMs: int
    lastSeenMs: int | None = None

    def public(self, connected: bool = False) -> dict[str, Any]:
        """As the settings page lists it: never the key."""
        return {
            "deviceId": self.deviceId,
            "name": self.name,
            "platform": self.platform,
            "pairedMs": self.pairedMs,
            "lastSeenMs": self.lastSeenMs,
            "connected": connected,
        }


class LinkStore:
    """``server.json`` (server ID, port, name, on/off) and ``devices.json``."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        _private_dir(directory)
        self._lock = threading.RLock()
        raw = _read_json(directory / SERVER_FILE)
        if isinstance(raw, dict) and str(raw.get("serverId") or ""):
            self._server: dict[str, Any] = {
                "serverId": str(raw["serverId"]),
                "port": int(raw.get("port") or 0),
                "name": str(raw.get("name") or ""),
                "enabled": bool(raw.get("enabled", False)),
                "trustNetwork": bool(raw.get("trustNetwork", False)),
            }
        else:
            self._server = {
                "serverId": str(uuid.uuid4()),
                "port": 0,
                "name": "",
                "enabled": False,
                "trustNetwork": False,
            }
            self._save_server()
        self._devices: list[Device] = []
        raw = _read_json(directory / DEVICES_FILE)
        for item in raw.get("devices", []) if isinstance(raw, dict) else []:
            try:
                self._devices.append(
                    Device(
                        deviceId=str(item["deviceId"]),
                        name=str(item.get("name") or item["deviceId"]),
                        platform=str(item.get("platform") or "unknown"),
                        apiKey=str(item["apiKey"]),
                        pairedMs=int(item.get("pairedMs") or 0),
                        lastSeenMs=item.get("lastSeenMs"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._saved_seen: dict[str, int] = {}

    # -- server.json --------------------------------------------------------

    def _save_server(self) -> None:
        _write_json(self.dir / SERVER_FILE, self._server)

    @property
    def server_id(self) -> str:
        return self._server["serverId"]

    @property
    def port(self) -> int:
        return self._server["port"]

    @property
    def enabled(self) -> bool:
        return self._server["enabled"]

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if self._server["enabled"] != on:
                self._server["enabled"] = on
                self._save_server()

    @property
    def trust_network(self) -> bool:
        """Devices on the local network connect without a PIN or a click
        (``POST pair/request`` is answered at once). Off unless turned on."""
        return self._server.get("trustNetwork", False)

    def set_trust_network(self, on: bool) -> None:
        with self._lock:
            if self._server.get("trustNetwork", False) != on:
                self._server["trustNetwork"] = on
                self._save_server()

    def set_server(self, port: int, name: str) -> None:
        with self._lock:
            if (self._server["port"], self._server["name"]) != (port, name):
                self._server["port"], self._server["name"] = port, name
                self._save_server()

    # -- devices.json -------------------------------------------------------

    def _save_devices(self) -> None:
        _write_json(
            self.dir / DEVICES_FILE, {"devices": [asdict(d) for d in self._devices]}
        )

    def devices(self) -> list[Device]:
        with self._lock:
            return list(self._devices)

    def upsert(self, device_id: str, name: str, platform: str) -> Device:
        """Pairs (or re-pairs) a device; a re-pair gets a new key, so the
        old one stops working."""
        with self._lock:
            record = Device(
                deviceId=device_id,
                name=name,
                platform=platform,
                apiKey=secrets.token_urlsafe(32),
                pairedMs=now_ms(),
            )
            previous = self._devices
            self._devices = [*(d for d in previous if d.deviceId != device_id), record]
            try:
                self._save_devices()
            except OSError:
                self._devices = previous
                raise
            return record

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            kept = [d for d in self._devices if d.deviceId != device_id]
            if len(kept) == len(self._devices):
                return False
            self._devices = kept
            self._save_devices()
            return True

    def find(self, matches: Callable[[str], bool]) -> Device | None:
        """The device whose key ``matches``. Every key is tried, so the time
        taken says nothing about which one matched."""
        with self._lock:
            found = None
            for device in self._devices:
                if matches(device.apiKey) and found is None:
                    found = device
            return found

    def by_key(self, candidate: str) -> Device | None:
        if not candidate:
            return None
        return self.find(
            lambda key: hmac.compare_digest(key.encode(), candidate.encode())
        )

    def touch(self, device_id: str) -> None:
        """Records that the device was just seen; the file only once a
        minute, so a busy phone does not rewrite it on every request."""
        now = now_ms()
        with self._lock:
            for device in self._devices:
                if device.deviceId == device_id:
                    device.lastSeenMs = now
            if now - self._saved_seen.get(device_id, 0) >= _TOUCH_INTERVAL_MS:
                self._saved_seen[device_id] = now
                try:
                    self._save_devices()
                except OSError:
                    logger.debug("[link] lastSeen not saved", exc_info=True)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class LinkAuthError(Exception):
    """A Subsonic error code (10 missing, 40 wrong credentials, 44 bad key)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def authenticate_subsonic(store: LinkStore, params: dict[str, str]) -> Device:
    """The device behind a Subsonic request: its key as ``apiKey``, as the
    password of ``t`` + ``s``, or as ``p`` (plain or ``enc:`` hex). Any
    username is accepted."""
    key = params.get("apiKey", "")
    if key:
        found = store.by_key(key)
        if not found:
            raise LinkAuthError(44, "Invalid API key")
        return found
    token, salt = params.get("t", ""), params.get("s", "")
    if token and salt:
        token = token.lower()
        found = store.find(
            lambda k: hmac.compare_digest(
                hashlib.md5((k + salt).encode("utf-8")).hexdigest().encode(),
                token.encode(),
            )
        )
        if not found:
            raise LinkAuthError(40, "Wrong username or password")
        return found
    password = params.get("p", "")
    if password:
        if password.startswith("enc:"):
            try:
                password = bytes.fromhex(password[4:]).decode("utf-8")
            except ValueError:
                raise LinkAuthError(40, "Wrong username or password") from None
        found = store.by_key(password)
        if not found:
            raise LinkAuthError(40, "Wrong username or password")
        return found
    raise LinkAuthError(10, "Required parameter is missing")


def authenticate_bearer(store: LinkStore, header: str | None) -> Device | None:
    scheme, _, key = (header or "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return store.by_key(key.strip())


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def pairing_proof(pin: str, fingerprint: str, device_id: str) -> str:
    message = f"{PROOF_CONTEXT}{fingerprint}:{device_id}".encode()
    return hmac.new(pin.encode("utf-8"), message, hashlib.sha256).hexdigest()


def new_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class PairError(Exception):
    """A refused ``POST /mornye/v1/pair``: ``rate_limited`` (429),
    ``pairing_closed`` / ``bad_proof`` (403), ``internal`` (500)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class _Window:
    pin: str
    expires_at: float
    attempts: int


class PairingWindow:
    def __init__(self, ttl_s: float = 300.0, interval_s: float = 1.0) -> None:
        self.ttl_s = ttl_s
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._window: _Window | None = None
        self._recent: dict[str, float] = {}
        # Why the last window ended, for the settings page.
        self.last_ended = ""

    def begin(self) -> dict[str, Any]:
        """Opens a new window with a fresh PIN, replacing any open one."""
        with self._lock:
            self._window = _Window(new_pin(), time.monotonic() + self.ttl_s, 0)
            self.last_ended = ""
            return {
                "pin": self._window.pin,
                "expiresMs": now_ms() + int(self.ttl_s * 1000),
            }

    def end(self) -> bool:
        with self._lock:
            was_open = self._window is not None
            self._window = None
            if was_open:
                self.last_ended = CANCELLED
            return was_open

    def _expire_locked(self) -> None:
        if self._window and time.monotonic() >= self._window.expires_at:
            self._window = None
            self.last_ended = EXPIRED

    def is_open(self) -> bool:
        with self._lock:
            self._expire_locked()
            return self._window is not None

    def current(self) -> dict[str, Any] | None:
        """The open window as the settings page shows it."""
        with self._lock:
            self._expire_locked()
            w = self._window
            if not w:
                return None
            left = w.expires_at - time.monotonic()
            return {"pin": w.pin, "expiresMs": now_ms() + int(left * 1000)}

    def attempt(
        self,
        peer: str,
        fingerprint: str,
        device_id: str,
        proof: str,
        store: Callable[[], T],
    ) -> T:
        """Checks one pairing request. With a valid proof ``store`` runs while
        the window is held, and the window closes only when it succeeds."""
        now = time.monotonic()
        with self._lock:
            self._recent = {
                p: at for p, at in self._recent.items() if now - at < self.interval_s
            }
            if peer in self._recent:
                raise PairError("rate_limited")
            if self.interval_s > 0:
                self._recent[peer] = now
            self._expire_locked()
            window = self._window
            if window is None:
                raise PairError("pairing_closed")
            expected = pairing_proof(window.pin, fingerprint, device_id)
            if not hmac.compare_digest(
                expected.encode(), proof.strip().lower().encode()
            ):
                window.attempts += 1
                if window.attempts >= MAX_ATTEMPTS:
                    self._window = None
                    self.last_ended = ATTEMPTS
                raise PairError("bad_proof")
            try:
                value = store()
            except Exception as exc:
                raise PairError(
                    "internal", f"The device could not be saved: {exc}"
                ) from exc
            self._window = None
            self.last_ended = PAIRED
            return value


def is_local_address(address: str) -> bool:
    """A private, loopback or link-local address: the local network."""
    import ipaddress

    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


@dataclass
class PairRequest:
    """A device asking to connect without a PIN, waiting for a click."""

    id: str
    deviceId: str
    name: str
    platform: str
    address: str
    expires_at: float
    status: str = "pending"  # approved, denied, expired
    record: Device | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        """As the settings page shows it (never the key)."""
        return {
            "id": self.id,
            "deviceId": self.deviceId,
            "name": self.name,
            "platform": self.platform,
            "address": self.address,
        }


class PairRequests:
    """``POST pair/request``: a device asks, a person allows it (or not) on
    this computer. At most MAX_PENDING_REQUESTS wait at once, one per
    address; an unanswered one expires after ``ttl_s``."""

    def __init__(self, ttl_s: float = 120.0) -> None:
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._requests: dict[str, PairRequest] = {}

    def _expire_locked(self) -> None:
        now = time.monotonic()
        for request_id, req in list(self._requests.items()):
            if req.status == "pending" and now >= req.expires_at:
                req.status = "expired"
                req.done.set()
            # An answered request is kept a little, for the device's wait.
            if req.status != "pending" and now >= req.expires_at + 60:
                del self._requests[request_id]

    def open(
        self, device_id: str, name: str, platform: str, address: str
    ) -> PairRequest:
        with self._lock:
            self._expire_locked()
            pending = [r for r in self._requests.values() if r.status == "pending"]
            for req in pending:
                if req.deviceId == device_id and req.address == address:
                    return req  # asked again: the same request
            if any(r.address == address for r in pending):
                raise PairError("rate_limited")
            if len(pending) >= MAX_PENDING_REQUESTS:
                raise PairError("rate_limited")
            req = PairRequest(
                id=secrets.token_urlsafe(18),
                deviceId=device_id,
                name=name,
                platform=platform,
                address=address,
                expires_at=time.monotonic() + self.ttl_s,
            )
            self._requests[req.id] = req
            return req

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            self._expire_locked()
            return [
                r.public() for r in self._requests.values() if r.status == "pending"
            ]

    def answer(
        self, request_id: str, allow: bool, store: Callable[[PairRequest], Device]
    ) -> bool:
        """Allows (``store`` pairs the device) or denies a waiting request."""
        with self._lock:
            self._expire_locked()
            req = self._requests.get(request_id)
            if req is None or req.status != "pending":
                return False
            if allow:
                req.record = store(req)
                req.status = "approved"
            else:
                req.status = "denied"
            req.done.set()
            return True

    def wait(self, request_id: str, device_id: str, timeout: float) -> PairRequest:
        """The request once answered, or as it is after ``timeout``."""
        with self._lock:
            self._expire_locked()
            req = self._requests.get(request_id)
            if req is None or req.deviceId != device_id:
                raise PairError("not_found")
        req.done.wait(max(0.0, min(timeout, req.expires_at - time.monotonic())))
        with self._lock:
            self._expire_locked()
        return req

    def clear(self) -> None:
        with self._lock:
            for req in self._requests.values():
                if req.status == "pending":
                    req.status = "expired"
                    req.done.set()
            self._requests.clear()


__all__ = [
    "ATTEMPTS",
    "CANCELLED",
    "EXPIRED",
    "MAX_DEVICE_ID",
    "PAIRED",
    "PROTOCOL",
    "Device",
    "Identity",
    "LinkAuthError",
    "LinkStore",
    "PairError",
    "PairRequest",
    "PairRequests",
    "PairingWindow",
    "authenticate_bearer",
    "authenticate_subsonic",
    "fingerprint_of",
    "is_local_address",
    "link_dir",
    "load_or_create_identity",
    "pairing_proof",
]
