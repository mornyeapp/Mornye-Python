"""api_mixins/link.py — Settings → Mornye Link: pairing the phone app.

The adapter between the settings page and link_server.py (see that module
for the protocol). Every call returns the same status object, so the page
redraws from one shape: on/off, the addresses to type on the phone, the
certificate's short fingerprint, the open PIN with its expiry, and the
paired devices (never their keys).

The server is one per process; in --web multi-user mode these calls are
admin-only (webapp.py's LINK_ADMIN_METHODS), since a paired phone sees the
whole library.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any


class LinkMixin:
    if TYPE_CHECKING:
        download_dir: str
        log: Any

    def _link_roots(self) -> list[str]:
        from ..core.env import env_value

        extra = env_value("MORNYE_LIBRARY_SCAN_DIRS", "") or ""
        folders = [
            getattr(self, "download_dir", ""),
            *(p.strip() for p in extra.split(",")),
        ]
        return list(dict.fromkeys(f for f in folders if f))

    def _link(self):
        from ..link_server import get_link_server

        return get_link_server(self._link_roots)

    def _link_autostart(self) -> None:
        """Starts the server when it was left on; off the caller's thread,
        since a first start creates the certificate."""

        def run() -> None:
            try:
                server = self._link()
                if server.store.enabled and not server.running():
                    status = server.start()
                    if status.get("error"):
                        self.log(
                            f"Mornye Link did not start: {status['error']}", "warn"
                        )
            except Exception as exc:
                self.log(f"Mornye Link did not start: {exc}", "warn")

        threading.Thread(target=run, name="mornye-link-start", daemon=True).start()

    def link_status(self) -> dict[str, Any]:
        return self._link().status()

    def link_set_enabled(self, on: bool) -> dict[str, Any]:
        return self._link().set_enabled(bool(on))

    def link_begin_pairing(self) -> dict[str, Any]:
        """A new 6-digit PIN for five minutes; starts the server if needed."""
        return self._link().begin_pairing()

    def link_end_pairing(self) -> dict[str, Any]:
        return self._link().end_pairing()

    def link_revoke_device(self, device_id: str) -> dict[str, Any]:
        """Unpairs a device: its key stops working at once."""
        return self._link().revoke(str(device_id or ""))
