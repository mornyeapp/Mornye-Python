"""What the artists you follow released recently.

Built on the Following list (`core.subscriptions`): for every enabled artist
subscription the discography listing is read — the cheap half of a check, no
track lists — and the entries dated inside the window are kept. Nothing is
downloaded and nothing is marked as seen; this is a view, not a sync.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from . import subscriptions

logger = logging.getLogger(__name__)

#: How long one artist's listing is reused. Releases land on a weekly rhythm,
#: so asking again within the half hour only costs requests.
CACHE_TTL_S = 30 * 60
MAX_CONCURRENCY = 4

_cache: dict[str, tuple[float, str, list[subscriptions.Release]]] = {}


def _parse_day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


async def _artist_releases(
    sub: subscriptions.Subscription, *, refresh: bool, client: Any = None
) -> tuple[str, list[subscriptions.Release]]:
    cached = _cache.get(sub.url)
    if cached and not refresh and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1], cached[2]
    name, releases = await subscriptions.list_releases_async(
        sub.url, sub.include_groups, client=client
    )
    _cache[sub.url] = (time.monotonic(), name, releases)
    return name, releases


async def weekly_releases_async(
    *,
    days: int = 7,
    owner: str | None = None,
    refresh: bool = False,
    today: date | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Releases dated within the last `days` days, newest first.

    Returns ``{"releases": [...], "unavailable": [artist names], "followed": n}``.
    An artist whose listing fails is reported in `unavailable` and does not
    stop the others.
    """
    now = today or date.today()
    since = now - timedelta(days=max(1, int(days)))
    artists = [
        s
        for s in subscriptions.list_all(owner=owner, enabled_only=True)
        if s.kind == "artist"
    ]
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    releases: list[dict[str, Any]] = []
    unavailable: list[str] = []

    async def one(sub: subscriptions.Subscription) -> None:
        async with sem:
            try:
                name, items = await _artist_releases(
                    sub, refresh=refresh, client=client
                )
            except Exception as exc:
                logger.warning("[weekly] %s unavailable: %s", sub.name or sub.url, exc)
                unavailable.append(sub.name or sub.url)
                return
        artist = name or sub.name
        for item in items:
            day = _parse_day(item.date) if item.date else None
            if day is None or not (since <= day <= now):
                continue
            releases.append({**item.to_dict(), "artist": artist, "day": day.isoformat()})

    await asyncio.gather(*(one(s) for s in artists))
    releases.sort(key=lambda r: (r["day"], r["artist"]), reverse=True)
    return {"releases": releases, "unavailable": unavailable, "followed": len(artists)}
