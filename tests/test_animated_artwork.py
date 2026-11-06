from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from mornye.application import post_processing
from mornye.core import animated_artwork
from mornye.core.models import DownloadResult, TrackMetadata
from mornye import downloader as dl

STREAM_URL = "https://mvod.itunes.apple.com/artwork/album.m3u8"
WEBP = b"RIFF\x04\x00\x00\x00WEBPVP8 "


def test_fetches_album_artwork_from_the_configured_api(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://artwork\.example/api/v1/artwork/search\?.*"),
        json={"url": STREAM_URL},
    )

    result = asyncio.run(
        animated_artwork.fetch_animated_artwork_url(
            "Artist & Guest",
            "Album / Deluxe",
            api_url="https://artwork.example/",
        )
    )

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.params["artist"] == "Artist & Guest"
    assert request.url.params["album"] == "Album / Deluxe"
    assert result == STREAM_URL


def test_a_missing_animated_artwork_is_not_an_error(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://artwork\.example/api/v1/artwork/search\?.*"),
        status_code=404,
    )

    assert (
        asyncio.run(
            animated_artwork.fetch_animated_artwork_url(
                "Artist",
                "Album",
                api_url="https://artwork.example",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "api_url",
    [
        "http://apple-music-animated-artworks-api:8080",
        "  http://apple-music-animated-artworks-api:8080/  ",
        "https://artwork.example:8443",
        "http://192.168.1.10:3000",
    ],
)
def test_accepts_self_hosted_api_urls_with_custom_ports(httpx_mock, api_url) -> None:
    # Regression for issue #259: Docker service URLs with a port were rejected.
    httpx_mock.add_response(json={"url": STREAM_URL})

    result = asyncio.run(
        animated_artwork.fetch_animated_artwork_url(
            "Artist", "Album", api_url=api_url
        )
    )

    request = httpx_mock.get_request()
    assert request is not None
    assert str(request.url).startswith(api_url.strip().rstrip("/") + "/api/v1/")
    assert result == STREAM_URL


@pytest.mark.parametrize(
    "api_url",
    [
        "ftp://apple-music-animated-artworks-api:8080",
        "file:///etc/passwd",
        "apple-music-animated-artworks-api:8080",
        "http://user:pass@artwork.example",
        "http://artwork.example:99999",
        "http://artwork.example:abc",
    ],
)
def test_rejects_invalid_api_urls(api_url) -> None:
    with pytest.raises(animated_artwork.AnimatedArtworkError, match="HTTP"):
        asyncio.run(
            animated_artwork.fetch_animated_artwork_url(
                "Artist", "Album", api_url=api_url
            )
        )


def test_rejects_stream_urls_outside_apple_domains(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://artwork\.example/api/v1/artwork/search\?.*"),
        json={"url": "https://127.0.0.1/private.m3u8"},
    )

    with pytest.raises(animated_artwork.AnimatedArtworkError, match="unsupported"):
        asyncio.run(
            animated_artwork.fetch_animated_artwork_url(
                "Artist",
                "Album",
                api_url="https://artwork.example",
            )
        )


def test_conversion_writes_webp_atomically_and_does_not_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "album" / "cover.webp"
    monkeypatch.setattr(
        animated_artwork.shutil, "which", lambda _name: "/usr/bin/ffmpeg"
    )

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args, **_kwargs):
        Path(args[-1]).write_bytes(WEBP)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert asyncio.run(
        animated_artwork.convert_stream_to_webp_async(STREAM_URL, destination)
    )
    assert destination.read_bytes() == WEBP
    assert not list(destination.parent.glob(".mornye-artwork-*"))
    assert not asyncio.run(
        animated_artwork.convert_stream_to_webp_async(STREAM_URL, destination)
    )
    assert destination.read_bytes() == WEBP


def test_post_processing_saves_album_artwork_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "track.flac"
    audio.touch()
    metadata = TrackMetadata(
        id="track",
        title="Track",
        artists="Track Artist",
        album="Album",
        album_artist="Album Artist",
    )
    calls: list[tuple[str, str, Path, str | None]] = []

    async def fake_download(
        artist: str,
        album: str,
        destination: Path,
        *,
        api_url: str | None = None,
    ) -> bool:
        calls.append((artist, album, destination, api_url))
        destination.write_bytes(WEBP)
        return True

    monkeypatch.setattr(
        animated_artwork,
        "download_animated_artwork_async",
        fake_download,
    )

    options = dl.DownloadOptions(
        output_dir=str(tmp_path),
        save_animated_artwork=True,
        animated_artwork_api_url="https://artwork.example.test",
    )
    result = DownloadResult.ok("provider", str(audio), "flac")
    asyncio.run(post_processing.apply_post_processing(result, metadata, options))
    asyncio.run(post_processing.apply_post_processing(result, metadata, options))

    assert calls == [
        (
            "Album Artist",
            "Album",
            tmp_path / "cover.webp",
            "https://artwork.example.test",
        )
    ]
    assert (tmp_path / "cover.webp").read_bytes() == WEBP


def test_animated_artwork_setting_defaults_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(animated_artwork.ARTWORK_ENABLED_ENV, "yes")
    assert animated_artwork.animated_artworks_enabled_by_default()

    monkeypatch.setenv(animated_artwork.ARTWORK_ENABLED_ENV, "false")
    assert not animated_artwork.animated_artworks_enabled_by_default()


def test_gui_settings_use_environment_default_without_overriding_saved_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mornye import app

    settings_file = tmp_path / "gui-settings.json"
    monkeypatch.setattr(app, "cache_path", lambda _name: settings_file)
    monkeypatch.setenv(animated_artwork.ARTWORK_ENABLED_ENV, "1")
    monkeypatch.setenv(
        animated_artwork.ARTWORK_API_URL_ENV,
        "https://artwork.example.test",
    )
    api = app.MornyeAPI.__new__(app.MornyeAPI)

    assert api.load_settings()["save_animated_artwork"] is True
    assert (
        api.load_settings()["animated_artwork_api_url"]
        == "https://artwork.example.test"
    )

    settings_file.write_text(
        '{"save_animated_artwork": false,'
        '"animated_artwork_api_url": "https://saved.example.test"}',
        encoding="utf-8",
    )
    settings = api.load_settings()
    assert settings["save_animated_artwork"] is False
    assert settings["animated_artwork_api_url"] == "https://saved.example.test"
