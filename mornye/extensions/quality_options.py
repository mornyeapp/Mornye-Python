"""Choose a JS extension's own quality id from Mornye's quality tiers.

Every JS extension declares the qualities it serves in its manifest's
``qualityOptions`` (``{"id", "label", "description", "kind"}``, where
``kind`` is ``lossless``, ``lossy`` or ``spatial``). Mornye itself speaks a
handful of canonical tiers (HI_RES_LOSSLESS, LOSSLESS, DOLBY_ATMOS…), and the
legacy provider tokens (Qobuz ``27/7/6``, ``FLAC``, ``best``…) still flow
through older call sites.

``pick_quality_option`` is the one place that turns any of those into an id
the extension actually declared, so an extension never receives a token it
does not understand. When the manifest declares nothing the request is
passed through unchanged — the extension then keeps its old contract.
"""

from __future__ import annotations

from typing import Any

from ..core.quality import normalize_quality

#: Canonical tier → ids to look for, best match first.
_TIER_PREFERENCES: dict[str, tuple[str, ...]] = {
    "DOLBY_ATMOS": ("DOLBY_ATMOS",),
    "HI_RES_LOSSLESS": ("HI_RES_LOSSLESS", "HI_RES", "LOSSLESS"),
    "HI_RES": ("HI_RES", "HI_RES_LOSSLESS", "LOSSLESS"),
    "LOSSLESS": ("LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"),
    "HIGH": ("HIGH", "LOW"),
    "LOW": ("LOW", "HIGH"),
}

_LOSSLESS_HINTS = ("flac", "lossless", "alac", "hi-res", "hires", "24-bit")


def quality_options(manifest: dict | None) -> list[dict[str, Any]]:
    """The manifest's ``qualityOptions`` that carry an id, in declared order."""
    raw = (manifest or {}).get("qualityOptions") or []
    if not isinstance(raw, list):
        return []
    return [
        option
        for option in raw
        if isinstance(option, dict) and str(option.get("id") or "").strip()
    ]


def is_atmos_option(option: dict[str, Any]) -> bool:
    """Whether an option is a Dolby Atmos stream (not merely surround)."""
    option_id = str(option.get("id") or "").upper()
    label = str(option.get("label") or "").lower()
    return option_id == "DOLBY_ATMOS" or (
        option.get("kind") == "spatial" and "atmos" in label
    )


def has_atmos(manifest: dict | None) -> bool:
    return any(is_atmos_option(option) for option in quality_options(manifest))


def _is_lossless(option: dict[str, Any]) -> bool:
    kind = option.get("kind")
    if kind:
        return kind == "lossless"
    text = f"{option.get('id', '')} {option.get('label', '')}".lower()
    return any(hint in text for hint in _LOSSLESS_HINTS)


def _is_lossy(option: dict[str, Any]) -> bool:
    return option.get("kind") == "lossy"


def pick_quality_option(
    options: list[dict[str, Any]],
    requested: str | None,
    override: str | None = None,
) -> str | None:
    """Return the declared id that best serves ``requested``.

    Order of precedence:

    1. ``override`` — the quality the user picked for this extension — when
       the extension declares it.
    2. ``requested`` itself when it already is one of the declared ids
       (case-insensitive), e.g. ``best`` or ``flac``.
    3. The canonical tier of ``requested`` mapped onto the declared ids:
       Atmos → the extension's Atmos option, lossless tiers → the closest
       lossless id or the first lossless option, lossy tiers → the first
       lossy option.
    4. The first declared option.

    Returns ``requested`` unchanged when there are no options at all, and
    ``None`` only when both are empty.
    """
    if not options:
        return requested

    ids = [str(option["id"]) for option in options]
    by_upper = {option_id.upper(): option_id for option_id in ids}

    if override:
        match = by_upper.get(str(override).strip().upper())
        if match:
            return match

    if requested:
        match = by_upper.get(str(requested).strip().upper())
        if match:
            return match

    tier = normalize_quality(requested or "")

    if tier == "DOLBY_ATMOS":
        for option in options:
            if is_atmos_option(option):
                return str(option["id"])
        tier = "HI_RES_LOSSLESS"

    for candidate in _TIER_PREFERENCES.get(tier, ()):
        if candidate in by_upper:
            return by_upper[candidate]

    if tier in ("HIGH", "LOW"):
        for option in options:
            if _is_lossy(option):
                return str(option["id"])
    else:
        for option in options:
            if _is_lossless(option):
                return str(option["id"])

    return ids[0]
