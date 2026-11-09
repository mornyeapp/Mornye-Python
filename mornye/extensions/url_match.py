"""Whether an extension's `urlHandler.patterns` claim a URL — anchored to the host.

Three places used to answer this three different ways: a bare substring
test (extensions/manager.py, core/metadata_fallback.py) and an unanchored
`re.search` (core/extension_metadata.py). Neither looked at where in the
URL the pattern landed, so `tidal.com` claimed `nottidal.com` and
`https://example.org/?next=tidal.com`, and because the first installed
extension in alphabetical order wins, one extension with a broad pattern
could take links meant for another. The mobile app anchors patterns to the
URL's host (`ExtensionManifest::matches_url`); this does the same.

Two pattern shapes are accepted, because both are in use:

- Plain, as the mobile registry writes them: ``open.spotify.com``,
  ``tidal.com/browse``, ``https://music.apple.com/``. The host must equal
  the URL's host or be a parent domain of it (``www.``/``m.`` subdomains
  match), and a path, if given, must prefix the URL's path. A pattern with
  no ``/`` ending in ``:`` is a URI scheme (``spotify:``).
- Regex, as docs/guide/python-extensions.md documents them
  (``soundcloud\\.com``, ``^https?://(www\\.)?deezer\\.com/``). The match
  must begin at the start of the URL, or at a label boundary of its host,
  and must reach at least the end of the host — so it can neither start
  inside another name nor stop short of a suffix like ``.evil.example``.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_REGEX_MARKERS = re.compile(r"[\\^$()\[\]|*+?{}]")
_BARE_HOST = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:[/?#]|$)", re.I)


@functools.lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.debug("[ext] ignoring invalid urlHandler pattern %r", pattern)
        return None


def _split(url: str) -> tuple[str, str, str] | None:
    """(stripped url, hostname, path), or None when there is no host."""
    text = (url or "").strip()
    if "://" not in text:
        # A link pasted without its scheme (`open.spotify.com/track/…`)
        # still names a host; a scheme URI (`spotify:track:…`) does not.
        if not _BARE_HOST.match(text):
            return None
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    return text, host, parts.path or "/"


def _host_covers(pattern_host: str, host: str) -> bool:
    return host == pattern_host or host.endswith(f".{pattern_host}")


def _plain_matches(pattern: str, url: str) -> bool:
    if "/" not in pattern and pattern.endswith(":"):
        return url.strip().lower().startswith(pattern)
    split = _split(url)
    if split is None:
        return False
    _, host, path = split
    rest = pattern.split("://", 1)[1] if "://" in pattern else pattern
    pattern_host, _, pattern_path = rest.partition("/")
    pattern_host = pattern_host.split(":", 1)[0].rstrip(".")
    if not pattern_host or not _host_covers(pattern_host, host):
        return False
    if not pattern_path:
        return True
    return path.lower().startswith("/" + pattern_path)


def _regex_matches(pattern: str, url: str) -> bool:
    regex = _compile(pattern)
    if regex is None:
        return False
    split = _split(url)
    if split is None:
        # A scheme URI (`spotify:track:…`) has no host to anchor to; only
        # a match from its very first character counts.
        return regex.match((url or "").strip()) is not None
    text, host, path = split

    # Anchored at the start of the URL itself (`^https?://host/…`): the
    # match has to get past the host.
    m = regex.match(text)
    if m is not None:
        host_at = text.lower().find(host)
        if host_at >= 0 and m.end() >= host_at + len(host):
            return True

    # Otherwise against "host/path", starting at a label boundary.
    target = host + path
    starts = [0] + [i + 1 for i, ch in enumerate(host) if ch == "."]
    for start in starts:
        m = regex.match(target, start)
        if m is not None and m.end() >= len(host):
            return True
    return False


def url_pattern_matches(pattern: Any, url: str) -> bool:
    """Whether one `urlHandler` pattern claims `url`."""
    pattern = str(pattern or "").strip()
    if not pattern or not url:
        return False
    if _REGEX_MARKERS.search(pattern):
        return _regex_matches(pattern, url)
    return _plain_matches(pattern.lower(), url)


def handler_matches(manifest: Any, url: str, *, require_enabled: bool = False) -> bool:
    """Whether a manifest's `urlHandler` claims `url`.

    A handler with ``"enabled": false`` never matches. A missing
    ``enabled`` counts as on unless `require_enabled` — extensions made with
    the scaffold omit it, and treating that as off would have silently
    stopped them handling their own links.
    """
    if not isinstance(manifest, dict):
        return False
    handler = manifest.get("urlHandler")
    if not isinstance(handler, dict):
        return False
    enabled = handler.get("enabled")
    if enabled is False or (require_enabled and not enabled):
        return False
    patterns = handler.get("patterns")
    if not isinstance(patterns, list):
        return False
    return any(url_pattern_matches(p, url) for p in patterns)
