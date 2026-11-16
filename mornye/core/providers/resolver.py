from __future__ import annotations

from dataclasses import dataclass, field

from mornye.core.config import DownloadRequest
from mornye.core.providers.manifest import ExtensionManifest


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    capabilities: frozenset[str] = field(
        default_factory=lambda: frozenset({"download"})
    )
    qualities: frozenset[str] = field(
        default_factory=lambda: frozenset({"LOSSLESS", "HI_RES_LOSSLESS"})
    )
    priority: int = 0
    healthy: bool = True
    enabled: bool = True

    @classmethod
    def from_manifest(
        cls, manifest: dict, *, name: str | None = None
    ) -> ProviderProfile:
        compatible = dict(manifest)
        if name and not compatible.get("id") and not compatible.get("name"):
            compatible["id"] = name
        compatible.setdefault("version", "0.0.0")
        parsed = ExtensionManifest.from_dict(compatible)
        return cls(
            name=name or parsed.id,
            capabilities=parsed.capabilities,
            qualities=parsed.qualities,
            priority=parsed.priority,
            healthy=parsed.healthy,
            enabled=parsed.enabled,
        )

    def supports(self, quality: str) -> bool:
        return (
            self.enabled
            and self.healthy
            and "download" in self.capabilities
            and (quality in self.qualities or "*" in self.qualities)
        )


@dataclass(frozen=True)
class ProviderCandidate:
    name: str
    priority: int
    capabilities: frozenset[str]
    qualities: frozenset[str]


class ProviderResolver:
    """Resolve enabled, healthy providers by capability and priority."""

    # Mirrors the Mobile download path: lossless catalogue providers first,
    # then lossy/long-tail extension fallbacks.  Installed extension priority
    # still overrides this list through manifest ``priority`` values.
    _provider_priority = [
        "tidal",
        "qobuz",
        "deezer",
        "apple",
        "amazon",
        "youtube",
    ]

    def __init__(self, profiles: list[ProviderProfile] | None = None) -> None:
        self._profiles = {profile.name: profile for profile in profiles or []}

    @classmethod
    def from_extensions(cls, extensions: list[object]) -> ProviderResolver:
        profiles = []
        for extension in extensions:
            manifest = getattr(extension, "manifest", None)
            if isinstance(manifest, dict):
                profiles.append(
                    ProviderProfile.from_manifest(
                        manifest,
                        name=getattr(extension, "name", None),
                    )
                )
        return cls(profiles)

    def resolve(self, request: DownloadRequest) -> list[str]:
        return [candidate.name for candidate in self.resolve_candidates(request)]

    @staticmethod
    def _service_key(service: str) -> str:
        value = (
            service.removeprefix("ext:")
            .removesuffix("-web")
            .removesuffix("-py")
            .lower()
        )
        if value.startswith("apple-"):
            return "apple"
        if value.startswith(("ytmusic", "youtube-music")):
            return "youtube"
        return value

    def resolve_candidates(self, request: DownloadRequest) -> list[ProviderCandidate]:
        quality = request.config.download.quality.upper()
        configured = [
            self._service_key(service) for service in request.config.download.services
        ]
        configured_services = set(configured)
        preferred = self._service_key(request.config.download.preferred_service or "")
        if preferred and configured_services and preferred not in configured_services:
            return []
        allow_provider_fallback = request.config.download.fallback_services
        configured_order = (
            {name: index for index, name in enumerate(configured)}
            if configured
            else {name: index for index, name in enumerate(self._provider_priority)}
        )
        if not self._profiles:
            candidates = [
                ProviderCandidate(
                    name=name,
                    priority=0,
                    capabilities=frozenset({"download"}),
                    qualities=frozenset({"LOSSLESS", "HI_RES_LOSSLESS"}),
                )
                for name in self._provider_priority
                if not configured_services
                or self._service_key(name) in configured_services
            ]
            if configured:
                candidates.sort(
                    key=lambda c: configured_order.get(c.name, len(configured_order))
                )
            return self._apply_preference(
                candidates, preferred, allow_provider_fallback
            )

        profiles: list[ProviderProfile] = [
            profile
            for profile in self._profiles.values()
            if profile.supports(quality)
            and (
                not configured_services
                or self._service_key(profile.name) in configured_services
            )
        ]
        profiles.sort(
            key=lambda profile: (
                0 if preferred and self._service_key(profile.name) == preferred else 1,
                -profile.priority,
                configured_order.get(
                    self._service_key(profile.name), len(configured_order)
                ),
                profile.name,
            )
        )
        candidates = [
            ProviderCandidate(
                name=profile.name,
                priority=profile.priority,
                capabilities=profile.capabilities,
                qualities=profile.qualities,
            )
            for profile in profiles
        ]
        return self._apply_preference(candidates, preferred, allow_provider_fallback)

    @staticmethod
    def _apply_preference(
        candidates: list[ProviderCandidate],
        preferred: str,
        allow_fallback: bool,
    ) -> list[ProviderCandidate]:
        if not preferred:
            return candidates
        preferred_candidates = [
            candidate
            for candidate in candidates
            if ProviderResolver._service_key(candidate.name) == preferred
        ]
        if not preferred_candidates:
            return []
        if not allow_fallback:
            return preferred_candidates
        preferred_names = {candidate.name for candidate in preferred_candidates}
        return preferred_candidates + [
            candidate
            for candidate in candidates
            if candidate.name not in preferred_names
        ]
