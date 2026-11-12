"""Upcoming concerts an extension reports for an artist.

Same contract as the mobile app (docs/artist_concerts.md in the Mornye repo):
a metadata extension may add an optional ``concerts`` list to the artist it
returns. This validates and normalises that list; the UI never sees an entry
that was not checked here.

Each event: ``location`` and a valid ISO-8601 ``start_at`` are required
(a bare date is fine when the time is not announced); ``id``, ``venue``,
``time_zone``, ``detail_id`` and ``url`` are optional. Events are de-duplicated
by id (or date/location/venue), sorted by date, and capped at 500.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

MAX_CONCERTS = 500

_START_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


def _text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


def _http_url(value: str) -> str:
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def _valid_start(raw: str) -> bool:
    match = _START_RE.match(raw)
    if not match:
        return False
    year, month, day = (int(match[i]) for i in (1, 2, 3))
    hour, minute = int(match[4] or 0), int(match[5] or 0)
    if hour > 23 or minute > 59:
        return False
    try:
        # Rejects impossible days such as 2026-02-31 instead of inventing one.
        datetime(year, month, day, hour, minute)
    except ValueError:
        return False
    return True


def _instant(event: dict[str, Any]) -> datetime:
    """When the event starts, comparable across offsets (naive = as written)."""
    raw = event["start_at"].replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.fromisoformat(raw[:10])
    return dt.replace(tzinfo=None) if dt.tzinfo is None else dt.astimezone().replace(tzinfo=None)


def normalize_concerts(value: Any) -> list[dict[str, Any]]:
    """The valid concerts of `value`, de-duplicated and sorted by date."""
    if not isinstance(value, list):
        return []
    events: dict[str, dict[str, Any]] = {}
    for item in value[:MAX_CONCERTS]:
        if not isinstance(item, dict):
            continue
        start = _text(item, "start_at")
        location = _text(item, "location")
        if not location or not _valid_start(start):
            continue
        venue = _text(item, "venue")
        event_id = _text(item, "id") or f"{start}|{location}|{venue}"
        events.setdefault(
            event_id,
            {
                "id": event_id,
                "start_at": start,
                "has_time": "T" in start,
                "time_zone": _text(item, "time_zone"),
                "location": location,
                "venue": venue,
                "url": _http_url(_text(item, "url")),
            },
        )
    return sorted(events.values(), key=_instant)
