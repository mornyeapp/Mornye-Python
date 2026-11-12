"""The per-extension "Allow local network access" switch.

_netguard.js keeps every extension off private and local addresses. A
self-hosted source — Jellyfin on ``192.168.1.20`` is the case that asked
for this — needs exactly one of them: the server the user typed into its
settings. Manifests that mark that setting ``"grantsNetwork": true`` get it
already (sandbox.network_grants); Jellyfin's does not, and expects the app
to offer a switch instead. This is the switch.

Turned on, the hosts of the URLs in the extension's non-secret text
settings are let through; nothing else on the LAN is. It lives in a file of
the host's own, not in the extension's ``settings.json``: the extension
must not be able to grant itself access, and "Reset to defaults" or an
update should not quietly take it away.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ..core.atomic_io import write_private_json
from ..core.paths import data_path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _file() -> Path:
    # Resolved on every call, so MORNYE_DATA_DIR set after import still counts.
    return data_path("extension_local_network.json")


def _load() -> dict[str, bool]:
    path = _file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable %s", path)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): True for name, value in raw.items() if value is True}


def is_allowed(ext_name: str) -> bool:
    return _load().get(str(ext_name or ""), False)


def set_allowed(ext_name: str, allowed: bool) -> None:
    name = str(ext_name or "").strip()
    if not name:
        raise ValueError("An extension name is required.")
    with _LOCK:
        current = _load()
        if allowed:
            current[name] = True
        else:
            current.pop(name, None)
        write_private_json(_file(), dict(sorted(current.items())))
