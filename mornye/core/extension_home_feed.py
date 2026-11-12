"""Home feeds from extensions (``capabilities.homeFeed`` / ``getHomeFeed``).

Explore only ever showed Spotify's feed. Nine installed extensions — Jellyfin
("Recently added", "Most played", playlists from the user's own server),
Apple Music, Amazon, YouTube Music and others — answer ``getHomeFeed()`` in
the mobile runtime's shape::

    {"success": true, "greeting": "Good evening",
     "sections": [{"uri", "title",
                   "items": [{"id", "uri", "type", "name", "artists",
                              "description", "cover_url"}]}]}

which is the shape Explore already draws. What the feed does not carry is a
link the URL box can open — ``jellyfin:album:<id>`` means nothing to it — so
each item gets an ``open_url`` here (see ``_open_url``).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from .extension_enrichment import _has_javascript, _installed_extensions, _provider

logger = logging.getLogger(__name__)

PREFIX = "ext:"


def has_home_feed(ext: Any) -> bool:
    caps = (getattr(ext, "manifest", None) or {}).get("capabilities")
    return (
        isinstance(caps, dict) and caps.get("homeFeed") is True and _has_javascript(ext)
    )


def home_feed_extensions(manager: Any = None) -> list[dict[str, str]]:
    """``[{"id": "ext:jellyfin", "name": "jellyfin", "label": "Jellyfin"}]``."""
    rows = [
        {
            "id": PREFIX + ext.name,
            "name": ext.name,
            "label": getattr(ext, "display_name", "") or ext.name,
        }
        for ext in _installed_extensions(manager)
        if has_home_feed(ext)
    ]
    rows.sort(key=lambda row: row["label"].lower())
    return rows


def _http(value: Any) -> str:
    if isinstance(value, dict):  # Spotify-style {"spotify": "https://…"}
        value = next(iter(value.values()), "")
    text = str(value or "").strip()
    return text if urlparse(text).scheme in ("http", "https") else ""


def _server_base(manifest: dict, settings: dict) -> str:
    """The first http(s) URL among the extension's non-secret text settings:
    the server of a self-hosted source."""
    for entry in manifest.get("settings") or []:
        if not isinstance(entry, dict) or entry.get("type") != "string":
            continue
        if entry.get("secret"):
            continue
        url = _http(settings.get(entry.get("key")) or entry.get("default"))
        if url:
            return url.rstrip("/")
    return ""


def _open_url(item: dict, manifest: dict, settings: dict) -> str:
    """A link the URL box can open, so a click lands where a pasted link would.

    In order: a web link the item carries; the manifest's
    ``shareUrlTemplates`` for the item's type; for a self-hosted source whose
    ``urlHandler`` matches a fragment (Jellyfin's ``#/details?id=``), the
    configured server plus that fragment; failing all of those the item's
    ``uri`` (``spotify:album:…`` is understood as it is).
    """
    for key in ("external_url", "external_urls", "url", "share_url", "uri"):
        url = _http(item.get(key))
        if url:
            return url
    item_id = str(item.get("id") or "").strip()
    kind = str(item.get("type") or "").strip()
    caps = manifest.get("capabilities") or {}
    templates = caps.get("shareUrlTemplates") if isinstance(caps, dict) else None
    if item_id and isinstance(templates, dict) and isinstance(templates.get(kind), str):
        return templates[kind].replace("{id}", item_id)
    handler = manifest.get("urlHandler") or {}
    patterns = handler.get("patterns") if isinstance(handler, dict) else None
    fragment = next(
        (p for p in patterns or [] if isinstance(p, str) and p.startswith("#")), ""
    )
    base = _server_base(manifest, settings) if fragment and item_id else ""
    if base:
        return f"{base}/web/{fragment}{item_id}"
    return str(item.get("uri") or "")


def normalise_feed(result: Any, manifest: dict, settings: dict) -> dict:
    if not isinstance(result, dict):
        return {"success": False, "error": "The extension returned no feed."}
    if result.get("success") is False:
        return {
            "success": False,
            "error": str(result.get("error") or "The extension has no feed."),
        }
    sections = []
    for section in result.get("sections") or []:
        if not isinstance(section, dict):
            continue
        items = []
        for item in section.get("items") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            items.append({**item, "open_url": _open_url(item, manifest, settings)})
        if items:
            sections.append({"title": str(section.get("title") or ""), "items": items})
    if not sections:
        return {"success": False, "error": "The feed is empty."}
    return {
        "success": True,
        "greeting": str(result.get("greeting") or ""),
        "sections": sections,
    }


def fetch_home_feed(
    provider_id: str, *, manager: Any = None, provider_factory: Any = None
) -> dict:
    """One extension's feed, normalised; ``{"success": False, "error"}`` on
    any failure, never an exception."""
    name = provider_id[len(PREFIX) :] if provider_id.startswith(PREFIX) else provider_id
    ext = next(
        (e for e in _installed_extensions(manager) if e.name == name),
        None,
    )
    if ext is None or not has_home_feed(ext):
        return {"success": False, "error": f"'{name}' has no home feed."}
    try:
        if manager is None:
            from ..extensions.manager import ExtensionManager

            manager = ExtensionManager(auto_install_downloads=False)
        settings = manager.load_settings(ext.name)
    except Exception:
        settings = {}
    try:
        result = _provider(ext.name, provider_factory)._call("getHomeFeed")
    except Exception as exc:
        logger.debug("[home/%s] getHomeFeed failed: %s", ext.name, exc)
        return {"success": False, "error": str(exc)}
    return normalise_feed(result, ext.manifest or {}, settings or {})
