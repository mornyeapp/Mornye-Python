"""Lyrics straight from KuGou, QQ Music and Genius.

`kugou`, `qq` and `genius` have long been offered by the CLI
(`--lyrics-providers`) and the TUI wizard, but nothing in core/lyrics.py
answered to those names: choosing them was silently the same as not
choosing them. The mobile app fetches all three directly from the services
themselves (4.9.5) rather than through a proxy; this does the same.

- KuGou: line-synced LRC, strong on Chinese-language catalogue.
- QQ Music: line-synced LRC, likewise.
- Genius: plain (unsynced) lyrics from the song page, every verse and
  chorus container kept, for anything the synced catalogues miss.

Each fetcher returns the lyrics text or "" — never raises — so it fits the
contract of `_PROVIDER_MAP` in core/lyrics.py.
"""

from __future__ import annotations

import base64
import html
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from .env import env_value
from .http import NetworkManager
from .text_match import artists_match, titles_match

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145.0.0.0 Safari/537.36"

# mobilecdn only answers over plain HTTP; the search carries no credentials.
_KUGOU_SEARCH = "http://mobilecdn.kugou.com/api/v3/search/song"
_KUGOU_LYRICS_SEARCH = "https://krcs.kugou.com/search"
_KUGOU_LYRICS_DOWNLOAD = "https://lyrics.kugou.com/download"

_QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
_QQ_LYRICS = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
_QQ_HEADERS = {"User-Agent": _UA, "Referer": "https://y.qq.com/"}

_GENIUS_SEARCH = "https://genius.com/api/search/song"
#: The documented API. Needs a client access token from
#: https://genius.com/api-clients, read from $MORNYE_GENIUS_TOKEN; without
#: one the public web search above is used, which Genius may refuse.
_GENIUS_API_SEARCH = "https://api.genius.com/search"
_GENIUS_TOKEN_ENV = "MORNYE_GENIUS_TOKEN"

#: A candidate further than this from the track's own duration is another
#: recording (a live take, an extended mix) or a preview.
_DURATION_TOLERANCE_S = 10


def _pick(
    candidates: list[dict[str, Any]],
    track_name: str,
    artist_name: str,
    duration_s: int,
) -> dict[str, Any] | None:
    """The best of `candidates` ({"title", "artist", "duration", ...}).

    Title and artist must match; among those the closest duration wins. A
    candidate with no duration is accepted only when the track's own
    duration is unknown too, or when nothing timed matched.
    """
    best: dict[str, Any] | None = None
    best_delta: float | None = None
    untimed: dict[str, Any] | None = None
    for item in candidates:
        title = str(item.get("title") or "")
        artist = str(item.get("artist") or "")
        if not titles_match(track_name, title):
            continue
        if artist_name and not artists_match(artist_name, artist):
            continue
        duration = item.get("duration") or 0
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0
        if not duration_s or not duration:
            if untimed is None:
                untimed = item
            continue
        delta = abs(duration - duration_s)
        if delta > _DURATION_TOLERANCE_S:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = item, delta
    return best or untimed


def _is_synced(text: str) -> bool:
    return bool(re.search(r"^\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", text, re.M))


# ---------------------------------------------------------------------------
# KuGou
# ---------------------------------------------------------------------------


def decode_kugou_content(content: str) -> str:
    """KuGou's `download` answer carries the LRC base64-encoded."""
    try:
        text = base64.b64decode(content).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""
    return text.lstrip("\ufeff").replace("\r\n", "\n").strip()


async def fetch_kugou_async(
    track_name: str,
    artist_name: str,
    duration_s: int = 0,
    timeout: int = 7,
) -> str:
    if not track_name:
        return ""
    try:
        client = await NetworkManager.get_async_client_safe()
        found = None
        # "artist title" first; KuGou's ranking sometimes buries a Western
        # title under that, so the bare title is the second try.
        for keyword in dict.fromkeys(
            [f"{artist_name} {track_name}".strip(), track_name.strip()]
        ):
            search = await client.get(
                _KUGOU_SEARCH,
                params={
                    "format": "json",
                    "keyword": keyword,
                    "page": "1",
                    "pagesize": "20",
                    "showtype": "1",
                },
                headers={"User-Agent": _UA},
                timeout=timeout,
            )
            if not search.is_success:
                continue
            info = ((search.json() or {}).get("data") or {}).get("info") or []
            candidates = [
                {
                    "title": s.get("songname", ""),
                    "artist": s.get("singername", ""),
                    "duration": s.get("duration", 0),
                    "hash": s.get("hash", ""),
                }
                for s in info
                if isinstance(s, dict) and s.get("hash")
            ]
            found = _pick(candidates, track_name, artist_name, duration_s)
            if found:
                break
        if not found:
            return ""

        duration_ms = int(float(found.get("duration") or duration_s or 0) * 1000)
        lyr = await client.get(
            _KUGOU_LYRICS_SEARCH,
            params={
                "ver": "1",
                "man": "yes",
                "client": "mobi",
                "keyword": "",
                "duration": str(duration_ms),
                "hash": found["hash"],
            },
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        if not lyr.is_success:
            return ""
        cands = (lyr.json() or {}).get("candidates") or []
        if not cands:
            return ""
        first = cands[0]
        dl = await client.get(
            _KUGOU_LYRICS_DOWNLOAD,
            params={
                "ver": "1",
                "client": "pc",
                "id": str(first.get("id", "")),
                "accesskey": str(first.get("accesskey", "")),
                "fmt": "lrc",
                "charset": "utf8",
            },
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        if not dl.is_success:
            return ""
        text = decode_kugou_content(str((dl.json() or {}).get("content") or ""))
        return text if _is_synced(text) else ""
    except Exception as exc:
        logger.debug("[lyrics/kugou] async: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# QQ Music
# ---------------------------------------------------------------------------


def decode_qq_lyric(value: str) -> str:
    """QQ's lyric field: plain with `nobase64=1`, base64 from older paths,
    and HTML entities (`&#58;` for ':') either way."""
    text = value or ""
    if text and not text.lstrip().startswith("[") and "&#" not in text:
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""
    return html.unescape(text).strip()


async def fetch_qq_async(
    track_name: str,
    artist_name: str,
    duration_s: int = 0,
    timeout: int = 7,
) -> str:
    if not track_name:
        return ""
    try:
        client = await NetworkManager.get_async_client_safe()
        search = await client.get(
            _QQ_SEARCH,
            params={
                "w": f"{track_name} {artist_name}".strip(),
                "format": "json",
                "p": "1",
                "n": "10",
                "cr": "1",
                "new_json": "1",
            },
            headers=_QQ_HEADERS,
            timeout=timeout,
        )
        if not search.is_success:
            return ""
        songs = (((search.json() or {}).get("data") or {}).get("song") or {}).get(
            "list"
        ) or []
        candidates = []
        for s in songs:
            if not isinstance(s, dict):
                continue
            singers = s.get("singer") or []
            candidates.append(
                {
                    "title": s.get("name") or s.get("songname") or "",
                    "artist": ", ".join(
                        str(x.get("name", "")) for x in singers if isinstance(x, dict)
                    ),
                    "duration": s.get("interval", 0),
                    "mid": s.get("mid") or s.get("songmid") or "",
                }
            )
        found = _pick(
            [c for c in candidates if c["mid"]], track_name, artist_name, duration_s
        )
        if not found:
            return ""
        lyr = await client.get(
            _QQ_LYRICS,
            params={
                "songmid": found["mid"],
                "format": "json",
                "nobase64": "1",
                "g_tk": "5381",
            },
            headers=_QQ_HEADERS,
            timeout=timeout,
        )
        if not lyr.is_success:
            return ""
        text = decode_qq_lyric(str((lyr.json() or {}).get("lyric") or ""))
        return text if _is_synced(text) else ""
    except Exception as exc:
        logger.debug("[lyrics/qq] async: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Genius
# ---------------------------------------------------------------------------

_GENIUS_CONTAINER_RE = re.compile(
    r'<div[^>]*data-lyrics-container="true"[^>]*>', re.IGNORECASE
)
_GENIUS_EXCLUDED_RE = re.compile(
    r'<div[^>]*data-exclude-from-selection="true"[^>]*>.*?</div>',
    re.IGNORECASE | re.DOTALL,
)
_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)


def _container_body(page: str, start: int) -> str:
    """The inner HTML of the <div> whose opening tag ends at `start`,
    following nested <div>s to the matching close."""
    depth = 1
    pos = start
    while True:
        m = _DIV_TAG_RE.search(page, pos)
        if not m:
            return page[start:]
        depth += -1 if m.group(1) else 1
        pos = m.end()
        if depth == 0:
            return page[start : m.start()]


def extract_genius_lyrics(page: str) -> str:
    """Every `data-lyrics-container` on a Genius song page, as plain text.

    A song's lyrics are split over several containers (one per section,
    with ads between them); taking only the first loses everything after
    the opening verse.
    """
    parts = []
    for m in _GENIUS_CONTAINER_RE.finditer(page or ""):
        body = _container_body(page, m.end())
        body = _GENIUS_EXCLUDED_RE.sub("", body)
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
        body = re.sub(r"<[^>]+>", "", body)
        text = html.unescape(body).strip()
        if text:
            parts.append(text)
    text = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def fetch_genius_async(
    track_name: str,
    artist_name: str,
    timeout: int = 7,
) -> str:
    if not track_name:
        return ""
    try:
        client = await NetworkManager.get_async_client_safe()
        query = f"{track_name} {artist_name}".strip()
        token = (env_value(_GENIUS_TOKEN_ENV, "") or "").strip()
        if token:
            search = await client.get(
                _GENIUS_API_SEARCH,
                params={"q": query, "per_page": "10"},
                headers={"User-Agent": _UA, "Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        else:
            search = await client.get(
                _GENIUS_SEARCH,
                params={"q": query, "per_page": "10"},
                headers={"User-Agent": _UA},
                timeout=timeout,
            )
        if not search.is_success:
            logger.debug("[lyrics/genius] search answered HTTP %s", search.status_code)
            return ""
        response = (search.json() or {}).get("response") or {}
        # /api/search/song nests hits in sections; api.genius.com/search
        # returns them flat.
        sections = response.get("sections") or [{"hits": response.get("hits") or []}]
        candidates = []
        for section in sections:
            for hit in (section or {}).get("hits") or []:
                result = (hit or {}).get("result") or {}
                url = str(result.get("url") or "")
                # Host and scheme parsed, not a string prefix: only a song
                # page on genius.com itself is ever fetched.
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.hostname != "genius.com":
                    continue
                candidates.append(
                    {
                        "title": result.get("title", ""),
                        "artist": (result.get("primary_artist") or {}).get("name", "")
                        or result.get("artist_names", ""),
                        "url": url,
                    }
                )
        found = _pick(candidates, track_name, artist_name, 0)
        if not found:
            return ""
        page = await client.get(
            found["url"],
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        if not page.is_success:
            return ""
        return extract_genius_lyrics(page.text)
    except Exception as exc:
        logger.debug("[lyrics/genius] async: %s", exc)
        return ""
