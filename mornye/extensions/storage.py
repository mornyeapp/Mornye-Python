"""Persistent, encrypted `storage` for JavaScript extensions.

`storage.get/set/delete` in _bridge.js used to live only in the Node
process's memory, so every restart forgot what an extension had saved — a
login token, a refreshed session, a device id — and the extension had to
start over. The mobile app persists the same API and encrypts it with a
platform key; this is the Python equivalent.

Layout: one file per extension under ``<data_dir>/extension_storage``,
``<name>.json.enc``, a Fernet token over the JSON object. The key lives in
the OS credential store when a secure one is available, and otherwise in a
``.master.key`` file next to the data (0600, directory 0700), which still
keeps the values out of plain-text backups and greps but is only as safe as
the account itself.

Node never touches these files: Python decrypts the snapshot and hands it
to the bridge at start-up, and the bridge sends each change back as a
``storage_write`` message (see runtime.py). That keeps _fsguard.js's write
allow-list unchanged.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..core.paths import data_path

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "mornye-extension-storage"
_KEYRING_USER = "master-key"
_SECURE_KEYRING_MODULES = (
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
)
#: The bridge holds the whole snapshot in an env var and in memory; a value
#: this large is a bug in the extension, not something to persist.
MAX_STORAGE_BYTES = 1024 * 1024

_key_lock = threading.Lock()
_cached_keys: dict[Path, bytes] = {}
_apply_locks: dict[Path, threading.Lock] = {}
_apply_locks_guard = threading.Lock()


def _apply_lock_for(path: Path) -> threading.Lock:
    with _apply_locks_guard:
        return _apply_locks.setdefault(path, threading.Lock())


def storage_dir() -> Path:
    return data_path("extension_storage")


def _safe_name(ext_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", ext_name.strip()).strip("._")
    return name or "extension"


def _secure_keyring() -> Any | None:
    if os.environ.get("MORNYE_EXT_STORAGE_NO_KEYRING") == "1":
        return None
    try:
        keyring = importlib.import_module("keyring")
        backend = keyring.get_keyring()
    except Exception:
        return None
    module = type(backend).__module__
    if getattr(backend, "priority", 0) <= 0 or not any(
        module == m or module.startswith(f"{m}.") for m in _SECURE_KEYRING_MODULES
    ):
        return None
    return keyring


def _write_private(path: Path, data: bytes) -> None:
    """Atomic write, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _master_key(base: Path) -> bytes:
    with _key_lock:
        cached = _cached_keys.get(base)
        if cached is not None:
            return cached

        key: bytes | None = None
        keyring = _secure_keyring()
        if keyring is not None:
            try:
                stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
                if stored:
                    key = stored.encode("ascii")
                else:
                    candidate = Fernet.generate_key()
                    keyring.set_password(
                        _KEYRING_SERVICE, _KEYRING_USER, candidate.decode("ascii")
                    )
                    key = candidate
            except Exception as exc:
                logger.warning(
                    "OS credential store unavailable for extension storage (%s); "
                    "using a key file instead.",
                    type(exc).__name__,
                )
                key = None

        if key is None:
            key_file = base / ".master.key"
            try:
                key = key_file.read_bytes().strip()
                Fernet(key)
            except (OSError, ValueError):
                key = Fernet.generate_key()
                _write_private(key_file, key)

        _cached_keys[base] = key
        return key


class ExtensionStorage:
    """The saved `storage` of one extension."""

    def __init__(self, ext_name: str, base_dir: Path | None = None) -> None:
        self.ext_name = ext_name
        self.base_dir = Path(base_dir) if base_dir is not None else storage_dir()
        self.path = self.base_dir / f"{_safe_name(ext_name)}.json.enc"
        self._lock = threading.Lock()

    def _fernet(self) -> Fernet:
        return Fernet(_master_key(self.base_dir))

    def load(self) -> dict[str, Any]:
        try:
            token = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.warning("[%s] could not read saved storage: %s", self.ext_name, exc)
            return {}
        try:
            data = json.loads(self._fernet().decrypt(token))
        except (InvalidToken, ValueError) as exc:
            # A key that changed (credential store reset, data copied to
            # another machine) makes the file unreadable; starting empty is
            # what the extension would have seen before this existed.
            logger.warning(
                "[%s] saved storage could not be decrypted (%s); starting empty.",
                self.ext_name,
                type(exc).__name__,
            )
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            return False
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > MAX_STORAGE_BYTES:
            logger.warning(
                "[%s] storage is %d bytes, over the %d limit; not saved.",
                self.ext_name,
                len(raw),
                MAX_STORAGE_BYTES,
            )
            return False
        with self._lock:
            if not data:
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()
                return True
            _write_private(self.path, self._fernet().encrypt(raw))
        return True

    def apply(self, op: str, key: str, value: Any = None) -> bool:
        """Applies one `storage.set`/`storage.delete` from a running bridge.

        Read-modify-write under the lock, against the file rather than a
        cached copy, so several runtimes of the same extension (the provider
        keeps a pool) each add their own keys instead of overwriting the
        others' with a stale snapshot.
        """
        if op not in ("set", "delete") or not isinstance(key, str):
            return False
        with _apply_lock_for(self.path):
            data = self.load()
            if op == "set":
                data[key] = value
            elif key in data:
                del data[key]
            else:
                return True
            return self.save(data)

    def clear(self) -> None:
        with self._lock, contextlib.suppress(FileNotFoundError):
            self.path.unlink()
