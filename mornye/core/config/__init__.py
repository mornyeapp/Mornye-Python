from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from pathlib import Path
from typing import Any

from mornye.core.animated_artwork import (
    artwork_api_url,
    animated_artworks_enabled_by_default,
)
from mornye.core.models import DownloadResult, TrackMetadata


@dataclass
class OutputConfig:
    directory: Path = Path("./Downloads")
    network_destination_id: str | None = None
    filename_format: str = "{title} - {artist}"
    artist_subfolders: bool = False
    album_subfolders: bool = False
    album_folder_year: bool = False
    playlist_subfolders: bool = True


@dataclass
class DownloadConfig:
    quality: str = "LOSSLESS"
    services: list[str] = field(default_factory=list)
    preferred_service: str | None = None
    fallback_services: bool = True
    allow_fallback: bool = True
    max_concurrent: int = 2
    retries: int = 0
    timeout: int | None = None
    resume: bool = True
    metadata_delay_min_s: float = 0.0
    metadata_delay_max_s: float = 0.0
    provider_delay_min_s: float = 0.0
    provider_delay_max_s: float = 0.0
    auto_rate_limit_cooldown: bool = True

    def __post_init__(self) -> None:
        for name in (
            "metadata_delay_min_s",
            "metadata_delay_max_s",
            "provider_delay_min_s",
            "provider_delay_max_s",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0")
            setattr(self, name, value)
        if self.metadata_delay_max_s < self.metadata_delay_min_s:
            raise ValueError("metadata_delay_max_s must be >= metadata_delay_min_s")
        if self.provider_delay_max_s < self.provider_delay_min_s:
            raise ValueError("provider_delay_max_s must be >= provider_delay_min_s")


@dataclass
class MetadataConfig:
    enrich: bool = True
    providers: list[str] = field(
        default_factory=lambda: ["deezer", "apple", "qobuz", "tidal"],
    )
    save_animated_artwork: bool = field(
        default_factory=animated_artworks_enabled_by_default,
    )
    animated_artwork_api_url: str = field(default_factory=artwork_api_url)


@dataclass
class LyricsConfig:
    enabled: bool = True
    providers: list[str] = field(
        default_factory=lambda: ["apple", "binilyrics", "lrclib"],
    )
    save_lrc: bool = False
    word_by_word: bool = True


@dataclass
class ExtensionConfig:
    registries: list[str] = field(default_factory=list)
    auto_update: bool = True
    minimum_trust: str = "UNVERIFIED"


@dataclass
class QueueConfig:
    persistent: bool = True
    max_jobs: int = 100
    retry_on_error: bool = True


@dataclass
class SecurityConfig:
    require_https: bool = True
    allow_unsigned_local: bool = True
    allow_unsigned_remote: bool = False


@dataclass
class MornyeConfig:
    output: OutputConfig = field(default_factory=OutputConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    lyrics: LyricsConfig = field(default_factory=LyricsConfig)
    extensions: ExtensionConfig = field(default_factory=ExtensionConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    @classmethod
    def from_legacy_options(cls, options: Any) -> MornyeConfig:
        """Build the new config model from the current downloader options."""
        config = cls()
        config.output.directory = Path(getattr(options, "output_dir", "./Downloads"))
        config.output.filename_format = getattr(
            options, "filename_format", "{title} - {artist}"
        )
        config.output.artist_subfolders = getattr(
            options, "use_artist_subfolders", False
        )
        config.output.album_subfolders = getattr(options, "use_album_subfolders", False)
        config.output.album_folder_year = getattr(options, "album_folder_year", False)
        config.output.playlist_subfolders = getattr(
            options, "create_playlist_subfolders", True
        )

        config.download.quality = getattr(options, "quality", "LOSSLESS")
        config.download.services = list(getattr(options, "services", []))
        config.download.preferred_service = getattr(options, "preferred_service", None)
        config.download.fallback_services = getattr(options, "fallback_services", True)
        config.download.allow_fallback = getattr(options, "allow_fallback", True)
        config.download.max_concurrent = getattr(options, "max_concurrent_downloads", 2)
        config.download.retries = getattr(options, "track_max_retries", 0)
        config.download.timeout = getattr(options, "timeout_s", None)
        config.download.resume = getattr(options, "resume", True)
        config.download.metadata_delay_min_s = getattr(
            options, "metadata_delay_min_s", 0.0
        )
        config.download.metadata_delay_max_s = getattr(
            options, "metadata_delay_max_s", 0.0
        )
        config.download.provider_delay_min_s = getattr(
            options, "provider_delay_min_s", 0.0
        )
        config.download.provider_delay_max_s = getattr(
            options, "provider_delay_max_s", 0.0
        )
        config.download.auto_rate_limit_cooldown = getattr(
            options, "auto_rate_limit_cooldown", True
        )

        config.metadata.enrich = getattr(options, "enrich_metadata", True)
        config.metadata.providers = list(
            getattr(options, "enrich_providers", config.metadata.providers)
        )
        config.metadata.save_animated_artwork = getattr(
            options,
            "save_animated_artwork",
            config.metadata.save_animated_artwork,
        )
        config.metadata.animated_artwork_api_url = getattr(
            options,
            "animated_artwork_api_url",
            config.metadata.animated_artwork_api_url,
        )

        config.lyrics.enabled = getattr(options, "embed_lyrics", True)
        config.lyrics.providers = list(
            getattr(options, "lyrics_providers", config.lyrics.providers)
        )
        config.lyrics.save_lrc = getattr(options, "save_lrc", False)
        config.lyrics.word_by_word = getattr(options, "apple_lyrics_word_by_word", True)

        config.extensions.registries = list(getattr(options, "registries", []))
        config.extensions.auto_update = True

        config.queue.persistent = True
        config.queue.max_jobs = 100
        config.queue.retry_on_error = True

        return config


#: The folder-layout keys of gui-settings.json, with the values the Settings
#: page starts from (frontend/app.js) when they were never saved.
_SAVED_LAYOUT_DEFAULTS: dict[str, Any] = {
    "filename_format": "{title} - {artist}",
    "use_artist_subfolders": True,
    "use_album_subfolders": True,
    "album_folder_year": False,
    "create_playlist_subfolders": True,
}


def saved_output_layout(settings: Any) -> dict[str, Any]:
    """The folder layout and filename format out of the saved GUI settings.

    Only those keys, so a caller can hand the result on (or persist it with a
    job) without carrying the rest of gui-settings.json — API keys included.
    A key never saved takes the Settings page's own default, so a download
    without the page matches a download from it.
    """
    source = settings if isinstance(settings, dict) else {}
    layout = dict(_SAVED_LAYOUT_DEFAULTS)
    for key, default in _SAVED_LAYOUT_DEFAULTS.items():
        value = source.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                layout[key] = value
        elif isinstance(value, str) and value.strip():
            layout[key] = value.strip()
    return layout


def apply_output_layout(output: OutputConfig, layout: Any) -> None:
    """Sets `output`'s folder layout from the saved GUI settings.

    For downloads that arrive without the Settings page (a REST client, an
    Apple Shortcut): MornyeConfig() alone writes every file loose into the
    download folder, while the page sends Artist/Album subfolders (issue #288).
    """
    resolved = saved_output_layout(layout)
    output.filename_format = resolved["filename_format"]
    output.artist_subfolders = resolved["use_artist_subfolders"]
    output.album_subfolders = resolved["use_album_subfolders"]
    output.album_folder_year = resolved["album_folder_year"]
    output.playlist_subfolders = resolved["create_playlist_subfolders"]


@dataclass
class DownloadRequest:
    sources: list[str]
    config: MornyeConfig
    prefetched: dict[str, TrackMetadata] | None = None


@dataclass
class DownloadSkip:
    track: TrackMetadata | None = None
    reason: str = ""
    provider: str | None = None
    source: str | None = None
    file_path: str | None = None


@dataclass
class DownloadFailure:
    track: TrackMetadata | None = None
    source: str | None = None
    reason: str = ""
    provider: str | None = None
    attempts: int = 0
    retryable: bool = False


@dataclass
class DownloadReport:
    succeeded: list[DownloadResult]
    failed: list[DownloadFailure]
    skipped: list[DownloadSkip] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed) + len(self.skipped)

    @property
    def success_count(self) -> int:
        return len(self.succeeded)


__all__ = [
    "DownloadConfig",
    "DownloadFailure",
    "DownloadReport",
    "DownloadRequest",
    "DownloadSkip",
    "ExtensionConfig",
    "LyricsConfig",
    "MetadataConfig",
    "OutputConfig",
    "QueueConfig",
    "SecurityConfig",
    "MornyeConfig",
]
