"""WebDAV destination delivery and read-only library access."""

from __future__ import annotations

import hashlib
import io
import logging
import posixpath
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from . import safe_xml
from .network_destinations import DestinationError, WebDAVProfile

logger = logging.getLogger(__name__)
_CHUNK_SIZE = 1024 * 1024


class WebDAVBackend:
    def __init__(
        self,
        profile: WebDAVProfile,
        username: str,
        password: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.profile = profile
        self.username = username
        self.password = password
        self._client = client

    @property
    def root(self) -> str:
        base = self.profile.url.rstrip("/")
        if self.profile.subdirectory:
            path = "/".join(
                quote(part, safe="") for part in self.profile.subdirectory.split("/")
            )
            return f"{base}/{path}"
        return base

    def _http(self) -> Any:
        if self._client is None:
            auth = (self.username, self.password) if self.username else None
            self._client = httpx.Client(
                auth=auth,
                timeout=self.profile.timeout,
                follow_redirects=False,
            )
        return self._client

    def _request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...],
        headers: dict[str, str] | None = None,
        content: Any = None,
        writing: bool = False,
    ) -> Any:
        try:
            response = self._http().request(
                method,
                url,
                headers=headers,
                content=content,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DestinationError(
                "The WebDAV server timed out. Check the URL and network connection."
            ) from exc
        except httpx.HTTPError as exc:
            raise DestinationError(
                "Could not connect to the configured WebDAV server."
            ) from exc
        if response.status_code not in expected:
            if response.status_code in {401, 403}:
                raise DestinationError(
                    "WebDAV access was denied. Check the account and collection permissions."
                )
            if response.status_code == 404:
                raise DestinationError(
                    "The WebDAV collection or file was not found. Check the server URL."
                )
            if writing:
                raise DestinationError(
                    "The WebDAV destination could not be written. Check permissions "
                    "and available storage."
                )
            raise DestinationError(
                f"The WebDAV server returned HTTP {response.status_code}."
            )
        return response

    def _url(self, path: str) -> str:
        parsed_root = urlsplit(self.root)
        parsed_target = urlsplit(path)
        if (
            parsed_target.scheme.lower() != parsed_root.scheme.lower()
            or parsed_target.netloc.lower() != parsed_root.netloc.lower()
        ):
            raise DestinationError(
                "The WebDAV path points outside the configured server."
            )
        root_path = unquote(urlsplit(self.profile.url).path).rstrip("/")
        target_path = unquote(parsed_target.path)
        if target_path != root_path and not target_path.startswith(root_path + "/"):
            raise DestinationError(
                "The WebDAV path points outside the configured collection."
            )
        if any(part in {".", ".."} for part in target_path.split("/")):
            raise DestinationError("The WebDAV path contains an unsafe component.")
        return path

    def _join(self, parent: str, name: str) -> str:
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or ":" in name
            or any(ord(char) < 32 for char in name)
        ):
            raise DestinationError("A WebDAV filename is unsafe.")
        return f"{parent.rstrip('/')}/{quote(name, safe='')}"

    @staticmethod
    def _local_name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    def _propfind(self, url: str) -> list[dict[str, Any]]:
        response = self._request(
            "PROPFIND",
            self._url(url),
            expected=(207,),
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
            content=(
                b'<?xml version="1.0" encoding="utf-8"?>'
                b'<d:propfind xmlns:d="DAV:"><d:prop>'
                b"<d:resourcetype/><d:getcontentlength/>"
                b"<d:getlastmodified/></d:prop></d:propfind>"
            ),
        )
        try:
            root = safe_xml.fromstring(response.content)
        except ET.ParseError as exc:
            raise DestinationError(
                "The WebDAV server returned an invalid listing."
            ) from exc
        entries = []
        origin = urlsplit(self.root)
        root_path = unquote(urlsplit(self.root).path).rstrip("/")
        for item in root.iter():
            if self._local_name(item) != "response":
                continue
            href = next(
                (child.text for child in item if self._local_name(child) == "href"),
                None,
            )
            if not href:
                continue
            target = urljoin(self.root.rstrip("/") + "/", href)
            parsed_target = urlsplit(target)
            if (
                parsed_target.scheme.lower() != origin.scheme.lower()
                or parsed_target.netloc.lower() != origin.netloc.lower()
            ):
                continue
            target_path = unquote(parsed_target.path).rstrip("/")
            if target_path != root_path and not target_path.startswith(root_path + "/"):
                continue
            prop = next(
                (node for node in item.iter() if self._local_name(node) == "prop"), None
            )
            if prop is None:
                continue
            is_directory = any(
                self._local_name(node) == "collection" for node in prop.iter()
            )
            length = next(
                (
                    node.text
                    for node in prop.iter()
                    if self._local_name(node) == "getcontentlength"
                ),
                "0",
            )
            modified = next(
                (
                    node.text
                    for node in prop.iter()
                    if self._local_name(node) == "getlastmodified"
                ),
                None,
            )
            entries.append(
                {
                    "url": target,
                    "directory": is_directory,
                    "size": int(length or 0),
                    "modified": modified,
                }
            )
        return entries

    def _make_directory(self, url: str) -> None:
        response = self._request(
            "MKCOL",
            self._url(url),
            expected=(201, 405),
            writing=True,
        )
        del response

    def _ensure_directory(self, url: str) -> None:
        root = self.profile.url.rstrip("/")
        target = self._url(url).rstrip("/")
        self._request("PROPFIND", root, expected=(207,))
        root_path = unquote(urlsplit(root).path).rstrip("/")
        target_path = unquote(urlsplit(target).path).rstrip("/")
        relative = target_path[len(root_path) :].strip("/")
        current = root
        for part in relative.split("/"):
            if not part:
                continue
            current = self._join(current, part)
            response = self._request("PROPFIND", current, expected=(207, 404))
            if response.status_code == 404:
                self._make_directory(current)

    def test_connection(self) -> dict[str, Any]:
        self._ensure_directory(self.root)
        probe = self._join(self.root, f".mornye-test-{uuid.uuid4().hex}.tmp")
        try:
            self._request(
                "PUT",
                probe,
                expected=(200, 201, 204),
                content=b"mornye webdav test",
                writing=True,
            )
            response = self._request("GET", probe, expected=(200,))
            if response.content != b"mornye webdav test":
                raise DestinationError("WebDAV write verification failed.")
        finally:
            try:
                self._request("DELETE", probe, expected=(200, 204, 404), writing=True)
            except DestinationError:
                logger.warning("Could not remove the temporary WebDAV test file.")
        return {
            "ok": True,
            "message": "WebDAV connection and write permissions verified.",
        }

    def list_audio_files(self) -> list[dict[str, Any]]:
        from .tagger import SUPPORTED_SUFFIXES

        pending = [self.root]
        visited: set[str] = set()
        found: list[dict[str, Any]] = []
        while pending:
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            for entry in self._propfind(directory):
                url = entry["url"].rstrip("/")
                if url == directory.rstrip("/"):
                    continue
                name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
                if entry["directory"]:
                    if name and not name.startswith("."):
                        pending.append(url)
                    continue
                suffix = posixpath.splitext(name)[1].lower()
                if suffix in SUPPORTED_SUFFIXES:
                    found.append(
                        {
                            "path": url,
                            "size": entry["size"],
                            "modified": entry["modified"],
                        }
                    )
        return sorted(found, key=lambda entry: entry["path"].casefold())

    def open_audio_file(self, path: str) -> Any:
        target = self._url(path)
        root_path = unquote(urlsplit(self.root).path).rstrip("/")
        target_path = unquote(urlsplit(target).path)
        if target_path != root_path and not target_path.startswith(root_path + "/"):
            raise DestinationError("The audio path is outside this WebDAV collection.")
        response = self._request("GET", target, expected=(200,))
        return io.BytesIO(response.content)

    def upload_tree(
        self,
        local_root: Path,
        *,
        progress: Callable[[str, int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        if not local_root.is_dir() or local_root.is_symlink():
            raise DestinationError("The local WebDAV staging directory is unavailable.")
        candidates = sorted(local_root.rglob("*"))
        if any(path.is_symlink() for path in candidates):
            raise DestinationError(
                "Staged destination contains an unsafe symbolic link."
            )
        files = [path for path in candidates if path.is_file()]
        self._ensure_directory(self.root)
        delivered: list[str] = []
        for local_path in files:
            if cancel_event is not None and cancel_event.is_set():
                raise DestinationError(
                    "WebDAV delivery was cancelled. Staged local files were retained."
                )
            relative = local_path.relative_to(local_root).as_posix()
            parts = relative.split("/")
            remote_dir = self.root
            for part in parts[:-1]:
                remote_dir = self._join(remote_dir, part)
                self._ensure_directory(remote_dir)
            delivered.append(
                self._upload_one(
                    local_path,
                    remote_dir,
                    cancel_event=cancel_event,
                    progress=(
                        (lambda sent, total: progress(relative, sent, total))
                        if progress
                        else None
                    ),
                )
            )
        return delivered

    def _upload_one(
        self,
        local_path: Path,
        remote_dir: str,
        *,
        cancel_event: threading.Event | None,
        progress: Callable[[int, int], None] | None,
    ) -> str:
        size, digest = _hash_local(local_path)
        for collision in range(1000):
            filename = (
                local_path.name
                if collision == 0
                else f"{local_path.stem} ({collision}){local_path.suffix}"
            )
            final_url = self._join(remote_dir, filename)
            try:
                response = self._request("HEAD", final_url, expected=(200, 404))
            except DestinationError:
                raise
            if response.status_code == 200:
                if int(response.headers.get("content-length", -1)) == size:
                    existing = self._request("GET", final_url, expected=(200,))
                    if hashlib.sha256(existing.content).hexdigest() == digest:
                        return final_url
                continue

            temporary_url = self._join(
                remote_dir, f".{filename}.{uuid.uuid4().hex}.mornye-part"
            )
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise DestinationError(
                        "WebDAV delivery was cancelled. Staged local files were retained."
                    )
                with local_path.open("rb") as source:
                    self._request(
                        "PUT",
                        temporary_url,
                        expected=(200, 201, 204),
                        content=source,
                        writing=True,
                    )
                if cancel_event is not None and cancel_event.is_set():
                    raise DestinationError(
                        "WebDAV delivery was cancelled. Staged local files were retained."
                    )
                uploaded = self._request("GET", temporary_url, expected=(200,))
                if (
                    len(uploaded.content) != size
                    or hashlib.sha256(uploaded.content).hexdigest() != digest
                ):
                    raise DestinationError("WebDAV upload verification failed.")
                move_result = self._request(
                    "MOVE",
                    temporary_url,
                    expected=(201, 204, 412),
                    headers={"Destination": final_url, "Overwrite": "F"},
                    writing=True,
                )
                if move_result.status_code == 412:
                    self._request(
                        "DELETE",
                        temporary_url,
                        expected=(200, 204, 404),
                        writing=True,
                    )
                    continue
                if progress:
                    progress(size, size)
                return final_url
            except Exception:
                try:
                    self._request(
                        "DELETE", temporary_url, expected=(200, 204, 404), writing=True
                    )
                except Exception:
                    logger.debug("Could not remove a WebDAV temporary file.")
                raise
        raise DestinationError(
            "Could not find an unused filename in the WebDAV collection."
        )


def _hash_local(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()
