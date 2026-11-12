"""A tiny local HTTP server that streams audio files to the player.

The desktop window cannot read ``file://`` paths from the page it serves, and
pywebview's own server only knows the frontend folder. This serves exactly
what the player asks for: bytes of an audio file, untouched, with HTTP Range
support so seeking works without reading the whole file.

Locked down on three sides: it binds to 127.0.0.1 only, every URL carries a
random per-run token, and a path is served only if the caller-supplied check
(the approved music folders) says so.
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
    def __init__(self, is_allowed: Callable[[Path], bool]) -> None:
        self._is_allowed = is_allowed
        self._token = secrets.token_urlsafe(18)
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
                path = Path(unquote(parts[2])).resolve()
                if not path.is_file() or not outer._is_allowed(path):
                    self.send_error(404)
                    return None
                return path

            def _serve(self, head_only: bool) -> None:
                path = self._resolve()
                if path is None:
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
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", content_type_for(path))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
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

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(
            target=self._httpd.serve_forever, name="mornye-media", daemon=True
        ).start()

    def url_for(self, path: str | Path) -> str:
        return f"http://127.0.0.1:{self.port}/{self._token}/{quote(str(path))}"

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
