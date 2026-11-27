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
            try:
                # The other computers this one is paired with (link_peers.py).
                self._link_peers().start()
            except Exception as exc:
                self.log(f"Could not reach the paired computers: {exc}", "warn")

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

    def link_answer_pair_request(self, request_id: str, allow: bool) -> dict[str, Any]:
        """Allows (or refuses) a device asking to connect without a PIN."""
        return self._link().answer_request(str(request_id or ""), bool(allow))

    def link_set_trust_network(self, on: bool) -> dict[str, Any]:
        """Devices on the local network connect without a PIN or a click."""
        return self._link().set_trust_network(bool(on))

    def link_revoke_device(self, device_id: str) -> dict[str, Any]:
        """Unpairs a device: its key stops working at once."""
        return self._link().revoke(str(device_id or ""))

    # -- other computers running Mornye (core/connect/link_peers.py) --------

    def _link_peers(self):
        from ..core.connect.link_peers import get_link_peers

        self._link()  # the peers live next to the server's files
        return get_link_peers()

    def link_computers(self) -> dict[str, Any]:
        """The computers this one is paired with, and how each is doing."""
        return {"computers": self._link_peers().status()}

    def link_find_computers(self) -> dict[str, Any]:
        """The computers announcing Mornye Link on this network (~3 s)."""
        return {"found": self._link_peers().discover()}

    def link_pair_computer(
        self, host: str, port: int, pin: str, fingerprint: str = ""
    ) -> dict[str, Any]:
        """Pairs with another computer showing ``pin`` in its settings."""
        from ..core.connect.link_peers import PeerError

        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "message": "The port must be a number."}
        host = str(host or "").strip()
        if not host or not 0 < port < 65536:
            return {"ok": False, "message": "Type the address shown there."}
        try:
            computer = self._link_peers().pair(
                host, port, str(pin or ""), str(fingerprint or "")
            )
        except PeerError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:
            self.log(f"Pairing with {host} failed: {exc}", "warn")
            return {"ok": False, "message": "Pairing failed."}
        return {"ok": True, "computer": computer, **self.link_computers()}

    def link_connect_computer(
        self, host: str, port: int, fingerprint: str = "", name: str = ""
    ) -> dict[str, Any]:
        """Asks another computer to connect without a PIN: someone allows it
        there (or it trusts this network). The list shows it waiting."""
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "message": "The port must be a number."}
        host = str(host or "").strip()
        if not host or not 0 < port < 65536:
            return {"ok": False, "message": "Type the address shown there."}
        self._link_peers().ask(host, port, str(fingerprint or ""), str(name or ""))
        return {"ok": True, **self.link_computers()}

    def link_remove_computer(self, server_id: str) -> dict[str, Any]:
        """Forgets a paired computer (it may still list this one: unpair it
        there too)."""
        self._link_peers().remove(str(server_id or ""))
        return self.link_computers()
