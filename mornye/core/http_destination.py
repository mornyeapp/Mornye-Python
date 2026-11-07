"""Plain HTTP(S) upload destination.

For a server that is neither SMB nor WebDAV: a small upload endpoint on a
NAS, a self-hosted file drop, a reverse proxy in front of a script. Each
delivered file is sent once, with the profile's method:

* ``PUT`` — the file is the request body, sent to the profile URL with the
  file's relative path appended (``https://nas.local/upload/Artist/Album/
  01 Song.flac``), or substituted for a ``{path}`` placeholder in the URL
  (``https://nas.local/files/{path}/raw``);
* ``POST`` — a multipart form sent to the profile URL itself, with the file
  in the ``file`` field and its relative path in the ``path`` field.

The relative path always starts with the profile's subfolder, if it has
one. Credentials, when set, go as HTTP Basic auth — prefer HTTPS for them.

An upload endpoint cannot be listed or read back, so this destination is
delivery-only: library scans need SMB or WebDAV.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from .network_destinations import (
    HTTP_PATH_PLACEHOLDER,
    DestinationError,
    HTTPProfile,
)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class _Transient(Exception):
    """A server answer worth retrying (overloaded, rate limited)."""


def _safe_parts(relative: str) -> list[str]:
    parts = str(relative or "").replace("\\", "/").split("/")
    if not parts or any(
        not part or part in {".", ".."} or any(ord(char) < 32 for char in part)
        for part in parts
    ):
        raise DestinationError("A staged filename is unsafe for HTTP delivery.")
    return parts


class HTTPUploadBackend:
    def __init__(
        self,
        profile: HTTPProfile,
        username: str,
        password: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.profile = profile
        self.username = username
        self.password = password
        self._client = client

    # -- addressing -----------------------------------------------------------

    def relative_path(self, relative: str) -> str:
        """The path a file is delivered under: subfolder + its own path."""
        parts = _safe_parts(relative)
        if self.profile.subdirectory:
            parts = self.profile.subdirectory.split("/") + parts
        return "/".join(parts)

    def target_url(self, relative: str) -> str:
        """Where a PUT for `relative` goes."""
        quoted = "/".join(
            quote(part, safe="") for part in self.relative_path(relative).split("/")
        )
        if HTTP_PATH_PLACEHOLDER in self.profile.url:
            return self.profile.url.replace(HTTP_PATH_PLACEHOLDER, quoted)
        return f"{self.profile.url.rstrip('/')}/{quoted}"

    @property
    def root(self) -> str:
        return self.profile.url

    # -- transport ------------------------------------------------------------

    def _http(self) -> Any:
        if self._client is None:
            auth = (self.username, self.password) if self.username else None
            self._client = httpx.Client(
                auth=auth,
                timeout=self.profile.timeout,
                follow_redirects=False,
            )
        return self._client

    def _check(self, response: Any) -> Any:
        status = response.status_code
        if 200 <= status < 300:
            return response
        if status in {401, 403}:
            raise DestinationError(
                "The upload server refused the credentials or the destination path."
            )
        if status == 404:
            raise DestinationError(
                "The upload URL was not found on the server. Check the URL."
            )
        if status == 405:
            raise DestinationError(
                f"The upload server does not accept {self.profile.method} at this URL."
            )
        if status == 413:
            raise DestinationError("The upload server rejected the file as too large.")
        if 300 <= status < 400:
            raise DestinationError(
                "The upload server answered with a redirect. Use the final URL "
                "(often https:// instead of http://)."
            )
        raise DestinationError(f"The upload server returned HTTP {status}.")

    def _send_once(self, relative: str, local_path: Path | None, data: bytes) -> str:
        if self.profile.method == "POST":
            target = self.profile.url
            path = self.relative_path(relative)
            name = path.rsplit("/", 1)[-1]
            form = {"path": path}
            if local_path is not None:
                with local_path.open("rb") as source:
                    response = self._http().post(
                        target, data=form, files={"file": (name, source)}
                    )
            else:
                response = self._http().post(
                    target, data=form, files={"file": (name, data)}
                )
        else:
            target = self.target_url(relative)
            if local_path is not None:
                with local_path.open("rb") as source:
                    response = self._http().put(target, content=source)
            else:
                response = self._http().put(target, content=data)
        if response.status_code in _TRANSIENT_STATUS:
            raise _Transient(f"HTTP {response.status_code}")
        self._check(response)
        if self.profile.method == "PUT":
            return target
        return f"{target} ({self.relative_path(relative)})"

    def _send(
        self,
        relative: str,
        local_path: Path | None = None,
        data: bytes = b"",
    ) -> str:
        last: Exception | None = None
        for _attempt in range(_MAX_ATTEMPTS):
            try:
                return self._send_once(relative, local_path, data)
            except (_Transient, httpx.TimeoutException, TimeoutError) as exc:
                last = exc
            except httpx.HTTPError as exc:
                raise DestinationError(
                    "Could not connect to the configured upload server."
                ) from exc
        if isinstance(last, (httpx.TimeoutException, TimeoutError)):
            raise DestinationError(
                "The upload server timed out. Check the URL and network connection."
            ) from last
        raise DestinationError(
            f"The upload server kept failing ({last}). Staged files were retained."
        ) from last

    # -- the destination interface -------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Upload a small probe file, then try to remove it again."""
        probe = f".mornye-test-{uuid.uuid4().hex}.txt"
        delivered = self._send(probe, data=b"mornye http upload test")
        message = "HTTP upload verified: the server accepted a test file."
        if self.profile.method == "PUT":
            try:
                response = self._http().delete(delivered)
                if response.status_code not in {200, 202, 204, 404}:
                    message += f" Remove {probe} from the server by hand."
            except Exception:
                message += f" Remove {probe} from the server by hand."
        else:
            message += f" A file named {probe} may be left on the server."
        return {"ok": True, "message": message}

    def upload_tree(
        self,
        local_root: Path,
        *,
        progress: Callable[[str, int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        if not local_root.is_dir() or local_root.is_symlink():
            raise DestinationError("The local staging directory is unavailable.")
        candidates = sorted(local_root.rglob("*"))
        if any(path.is_symlink() for path in candidates):
            raise DestinationError(
                "Staged destination contains an unsafe symbolic link."
            )
        delivered: list[str] = []
        for local_path in (path for path in candidates if path.is_file()):
            if cancel_event is not None and cancel_event.is_set():
                raise DestinationError(
                    "HTTP delivery was cancelled. Staged local files were retained."
                )
            relative = local_path.relative_to(local_root).as_posix()
            size = local_path.stat().st_size
            delivered.append(self._send(relative, local_path))
            if progress:
                progress(relative, size, size)
        return delivered

    def list_audio_files(self) -> list[dict[str, Any]]:
        raise DestinationError(
            "An HTTP upload destination cannot be browsed. Use SMB or WebDAV "
            "to scan a library."
        )

    def open_audio_file(self, path: str) -> Any:
        del path
        return self.list_audio_files()
