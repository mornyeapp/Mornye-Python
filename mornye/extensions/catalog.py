"""Extension-first service discovery and backwards-compatible aliases."""

from __future__ import annotations

from .manager import ExtensionManager

# Public service names historically accepted by the CLI/API, mapped to the
# JS extension (Mornye-Extension) that implements each service. The JS
# extension is always the primary provider; an installed Python extension
# for the same service (e.g. ``tidal-py``) is only tried after it, as a
# fallback — see application/provider_factory.build_providers_for_name.
SERVICE_ALIASES = {
    "tidal": "tidal-web",
    "qobuz": "qobuz-web",
    "amazon": "amazon",
    "apple": "apple-music",
    "deezer": "deezer",
    "soundcloud": "soundcloud",
    "youtube": "ytmusic-spotiflac",
    "youtube-music": "ytmusic-spotiflac",
    "gdstudio": "gdstudio",
    "pandora": "pandora",
}


def extension_id(service: str, manager: ExtensionManager | None = None) -> str:
    """Resolve ``ext:<id>`` and legacy service names to an installed extension.

    An installed extension with the exact service name wins over the historical
    alias.  This makes third-party extensions first-class without hard-coding
    them in the application.
    """
    value = service.removeprefix("ext:")
    if manager and manager.get_installed(value):
        return value
    return SERVICE_ALIASES.get(value, value)


def known_service(service: str) -> bool:
    """Return whether a CLI service spelling is valid without doing I/O."""
    return service.startswith("ext:") or service in SERVICE_ALIASES


def canonical_service_name(ext_name: str) -> str | None:
    """Normalize extension IDs such as ``tidal-web`` and ``tidal-py`` to one service name.

    Two extensions can provide one service — the JS and the Python Tidal
    providers both mean "Tidal" to someone choosing a source — so anything
    that offers the user a list of *services* has to collapse them first.
    """
    value = (ext_name or "").lower().removeprefix("ext:")
    if not value:
        return None

    value = value.replace("_", "-")
    value = value.replace("-web", "").replace("-py", "")

    # The Mobile extension is named YouTube Music, while the downloader's
    # public service id is kept as ``youtube`` for compatibility with the
    # legacy Python provider and its quality mapping.
    if value.startswith("ytmusic") or value == "youtube-music":
        return "youtube"

    alias_reverse = {v.lower(): k for k, v in SERVICE_ALIASES.items()}
    if value in alias_reverse:
        return alias_reverse[value]

    for prefix, service in (
        ("ytmusic", "youtube"),
        ("apple", "apple"),
        ("tidal", "tidal"),
        ("qobuz", "qobuz"),
        ("deezer", "deezer"),
        ("soundcloud", "soundcloud"),
        ("pandora", "pandora"),
        ("amazon", "amazon"),
    ):
        if value.startswith(prefix):
            return service
    return value


def installed_download_services(
    manager: ExtensionManager | None = None,
) -> list[dict[str, object]]:
    """The download services this install can actually offer, one per row.

    The single source of truth for every "choose your providers" surface:
    the terminal UI, and the Settings list in the GUI. They used to answer
    the question their own way — the guided mode from the installed
    extensions, the GUI from a list hard-coded in app.js — so a fresh
    install offered twelve services in Settings and only the installed ones
    on the command line, and a third-party provider appeared in neither.

    Returns ``[{"id", "label", "extensions", "primary", "quality_options",
    "atmos"}]``, sorted by id. ``primary`` is the extension that downloads
    first — the JS one whenever the service has one, the Python extension
    being only its fallback — and ``quality_options`` / ``atmos`` come from
    that extension's manifest. Never raises: a broken extension directory
    means an empty list and a caller that says so, not a UI that cannot open.
    """
    try:
        ext_manager = manager or ExtensionManager(auto_install_downloads=False)
        installed = ext_manager.list_installed()
    except Exception:
        return []

    services: dict[str, list[object]] = {}
    for ext in installed:
        if not getattr(ext, "is_download_provider", False):
            continue
        service = canonical_service_name(getattr(ext, "name", ""))
        if not service:
            continue
        services.setdefault(service, []).append(ext)

    rows: list[dict[str, object]] = []
    for service, extensions in sorted(services.items()):
        primary = _primary_extension(extensions)
        rows.append(
            {
                "id": service,
                "label": service_label(service),
                "extensions": sorted(str(getattr(e, "name", "")) for e in extensions),
                "primary": str(getattr(primary, "name", "")),
                "quality_options": [
                    {
                        "id": str(option.get("id")),
                        "label": str(option.get("label") or option.get("id")),
                        "description": str(option.get("description") or ""),
                        "kind": str(option.get("kind") or ""),
                    }
                    for option in (getattr(primary, "quality_options", None) or [])
                ],
                "atmos": bool(getattr(primary, "has_atmos", False)),
            }
        )
    return rows


def _primary_extension(extensions: list[object]) -> object:
    """The extension a service downloads with first: JS before Python."""
    for ext in extensions:
        if getattr(ext, "runtime", "javascript") != "python":
            return ext
    return extensions[0]


def atmos_service_ids(manager: ExtensionManager | None = None) -> set[str]:
    """Services whose primary extension declares a Dolby Atmos quality.

    Falls back to ``{"tidal"}`` when the extensions cannot be read (or none
    declares Atmos), the answer Mornye gave before extensions declared
    their own qualities.
    """
    found = {
        str(row["id"]) for row in installed_download_services(manager) if row["atmos"]
    }
    return found or {"tidal"}


def installed_service_ids(manager: ExtensionManager | None = None) -> list[str]:
    """Just the ids from `installed_download_services()`, for menus.

    Every guided frontend needs the same thing — a list of provider ids to
    offer — and each one used to unwrap the rows itself. One function, so a
    provider that appears in one menu appears in all of them.
    """
    return [str(service["id"]) for service in installed_download_services(manager)]


#: Spellings a title-cased id gets wrong.
_SERVICE_LABELS = {
    "amazon": "Amazon Music",
    "apple": "Apple Music",
    "gdstudio": "GDStudio",
    "joox": "JOOX",
    "netease": "NetEase",
    "qobuz": "Qobuz",
    "qq": "QQ Music",
    "soundcloud": "SoundCloud",
    "tidal": "Tidal",
    "youtube": "YouTube Music",
}


def service_label(service: str) -> str:
    """A service id as it should read in a menu."""
    if service in _SERVICE_LABELS:
        return _SERVICE_LABELS[service]
    return " ".join(part.capitalize() for part in service.replace("-", " ").split())
