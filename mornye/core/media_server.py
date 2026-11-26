"""A tiny local HTTP server that streams audio files to the player.

The desktop window cannot read ``file://`` paths from the page it serves, and
pywebview's own server only knows the frontend folder. This serves exactly
what the player asks for: bytes of an audio file, untouched, with HTTP Range
support so seeking works without reading the whole file.

Locked down on three sides: it binds to 127.0.0.1 only, every URL carries a
random per-run token, and a path is served only if the caller-supplied check
(the approved music folders) says so.

Casting (core/cast) runs a second one on the LAN address a receiver can
reach (`get_cast_media_server`): a DLNA/UPnP renderer or an FCast receiver
plays URLs, not bytes. Renderers probe before they play: a HEAD, a Range,
an OPTIONS, and the DLNA headers (``getcontentFeatures.dlna.org``,
``transferMode.dlna.org``), which it answers as Mornye mobile's media server
does: byte ranges yes, time seeking no (406).
"""

from __future__ import annotations

import hmac
import logging
import mimetypes
import re
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)

_CHUNK = 64 * 1024
_MAX_REGISTERED = 4096  # files remembered at once; the oldest is forgotten
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_EXTRA_TYPES = {
    ".flac": "audio/flac",
    ".opus": "audio/ogg; codecs=opus",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".mp3": "audio/mpeg",
}


def content_type_for(path: Path) -> str:
    return (
        _EXTRA_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


#: DLNA content features of an audio answer: byte ranges but no time
#: seeking, not transcoded, streaming and background transfer, DLNA 1.5.
DLNA_FEATURES = (
    "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
)
#: The same for pictures, which are fetched interactively.
DLNA_IMAGE_FEATURES = (
    "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=00F00000000000000000000000000000"
)
_TRANSFER_MODES = ("Streaming", "Interactive", "Background")


def dlna_features(mime: str) -> str:
    return DLNA_IMAGE_FEATURES if mime.startswith("image/") else DLNA_FEATURES


def dlna_headers(mime: str, asked_mode: str | None) -> dict[str, str]:
    """The DLNA headers of an answer of type `mime`: the transfer mode the
    renderer asked for (when it is one), else the fitting one."""
    fitting = "Interactive" if mime.startswith("image/") else "Streaming"
    mode = next(
        (m for m in _TRANSFER_MODES if m.lower() == (asked_mode or "").strip().lower()),
        fitting,
    )
    return {
        "transferMode.dlna.org": mode,
        "contentFeatures.dlna.org": dlna_features(mime),
    }


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive for a single ``bytes=`` range, else None.

    Raises ValueError for a range that cannot be satisfied (HTTP 416).
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # suffix: the last N bytes
        length = int(last)
        if length == 0:
            raise ValueError("empty suffix range")
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("range not satisfiable")
    return start, end


class MediaServer:
    def __init__(
        self,
        is_allowed: Callable[[Path], bool],
        host: str = "127.0.0.1",
        *,
        dlna: bool = False,
    ) -> None:
        self._is_allowed = is_allowed
        self.host = host
        # Answer every request as DLNA renderers expect, not only those that
        # ask with a *.dlna.org header.
        self._dlna = dlna
        self._token = secrets.token_urlsafe(18)
        self._paths: dict[str, Path] = {}
        self._keys: dict[Path, str] = {}
        self._paths_lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # silence stderr
                return

            def _resolve(self) -> Path | None:
                parts = urlsplit(self.path).path.split("/", 2)
                if len(parts) != 3 or not hmac.compare_digest(parts[1], outer._token):
                    self.send_error(403)
                    return None
                # The URL names a file by an opaque id handed out by url_for();
                # the request never supplies a path, only a key into the
                # table of files this process chose to serve.
                path = outer._lookup(unquote(parts[2]).partition(".")[0])
                if path is None:
                    self.send_error(404)
                    return None
                try:
                    # Still checked against the approved folders at serve
                    # time: a folder removed since the URL was minted stops
                    # being served.
                    allowed = outer._is_allowed(path) and path.is_file()
                except (OSError, RuntimeError, ValueError):
                    allowed = False
                if not allowed:
                    self.send_error(404)
                    return None
                return path

            def _dlna_extra(self, mime: str) -> dict[str, str]:
                asked = any(
                    k.lower().endswith(".dlna.org") for k in self.headers.keys()
                )
                if not (asked or outer._dlna):
                    return {}
                return dlna_headers(mime, self.headers.get("transferMode.dlna.org"))

            def _serve(self, head_only: bool) -> None:
                path = self._resolve()
                if path is None:
                    return
                # Only byte ranges are offered; a time range is refused.
                if self.headers.get("TimeSeekRange.dlna.org"):
                    self.send_response(406)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                size = path.stat().st_size
                try:
                    rng = parse_range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = rng if rng else (0, size - 1)
                mime = content_type_for(path)
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                for name, value in self._dlna_extra(mime).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head_only or size == 0:
                    return
                try:
                    with path.open("rb") as fh:
                        fh.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = fh.read(min(_CHUNK, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the player seeked or stopped; not an error

            def do_GET(self) -> None:
                self._serve(False)

            def do_HEAD(self) -> None:
                self._serve(True)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Allow", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._httpd = ThreadingHTTPServer((host, 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(
            target=self._httpd.serve_forever, name="mornye-media", daemon=True
        ).start()

    def _lookup(self, key: str) -> Path | None:
        with self._paths_lock:
            return self._paths.get(key)

    def url_for(self, path: str | Path) -> str:
        resolved = Path(path).resolve()
        with self._paths_lock:
            key = self._keys.get(resolved)
            if key is None:
                key = secrets.token_hex(8)
                self._keys[resolved] = key
                self._paths[key] = resolved
                if len(self._paths) > _MAX_REGISTERED:  # oldest first
                    old_key = next(iter(self._paths))
                    self._keys.pop(self._paths.pop(old_key), None)
        # The extension is cosmetic (some players sniff it); lookup ignores it.
        return (
            f"http://{self.host}:{self.port}/{self._token}/{key}"
            f"{quote(resolved.suffix)}"
        )

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


_lock = threading.Lock()
_server: MediaServer | None = None


def get_media_server(is_allowed: Callable[[Path], bool]) -> MediaServer:
    """The process-wide server, started on first use."""
    global _server
    with _lock:
        if _server is None:
            _server = MediaServer(is_allowed)
        return _server


_cast_servers: dict[str, MediaServer] = {}


def get_cast_media_server(is_allowed: Callable[[Path], bool], host: str) -> MediaServer:
    """A server on `host`, the LAN address a cast receiver reaches this
    computer at, answering as DLNA renderers expect. One per address."""
    with _lock:
        server = _cast_servers.get(host)
        if server is None:
            server = _cast_servers[host] = MediaServer(is_allowed, host, dlna=True)
        return server
