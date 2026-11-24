"""Issue #288: POST /api/v1/downloads wrote to ./Downloads, not the account's folder.

MornyeConfig() defaults its folder to ./Downloads, relative to the working
directory: /app/Downloads in the Docker image. A REST download of a
multi-user account (and a single-user one without output_dir) landed there
instead of the download folder the Web UI uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mornye.application.api_adapter import ApiAdapter
from mornye.core.config import DownloadReport
from mornye.core.models import DownloadResult
from mornye.webapi import ApiDeps, build_v1_router
from mornye.webapp import queued_url_config


def test_a_queued_job_downloads_into_the_accounts_own_folder():
    config = queued_url_config({}, "/app/downloads/alice-1a2b3c")

    assert config.output.directory == Path("/app/downloads/alice-1a2b3c")


def test_a_queued_job_keeps_its_quality():
    config = queued_url_config({"quality": "HI_RES_LOSSLESS"}, "/app/downloads/a")

    assert config.download.quality == "HI_RES_LOSSLESS"
    assert config.output.directory == Path("/app/downloads/a")


def test_a_queued_job_expands_a_folder_written_with_a_tilde():
    config = queued_url_config({}, "~/Music/Mornye")

    assert config.output.directory == Path("~/Music/Mornye").expanduser()


def _single_user_client(download_dir: str, settings: dict | None = None):
    seen: dict = {}

    class FakeDownloadService:
        async def download(self, request):
            seen["output_dir"] = request.config.output.directory
            seen["output"] = request.config.output
            return DownloadReport(
                succeeded=[DownloadResult.ok("tidal", "/tmp/example.flac")],
                failed=[],
                skipped=[],
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )

    app = FastAPI()
    app.include_router(
        build_v1_router(
            ApiDeps(
                api_for=lambda _request: SimpleNamespace(
                    download_dir=download_dir,
                    load_settings=lambda: dict(settings or {}),
                ),
                download_service=FakeDownloadService(),
                job_queue=None,
            )
        )
    )
    return TestClient(app), seen


def test_single_user_without_output_dir_uses_the_configured_download_folder():
    client, seen = _single_user_client("/srv/music")

    response = client.post(
        "/api/v1/downloads", json={"url": "https://open.spotify.com/track/x"}
    )

    assert response.status_code == 202
    assert seen["output_dir"] == Path("/srv/music")


def test_single_user_output_dir_still_wins():
    client, seen = _single_user_client("/srv/music")

    client.post(
        "/api/v1/downloads",
        json={"url": "https://open.spotify.com/track/x", "output_dir": "/elsewhere"},
    )

    assert seen["output_dir"] == Path("/elsewhere")


# ── Folder layout (issue #288, follow-up) ────────────────────────────────────
# A REST client (an Apple Shortcut) sends a URL and nothing about folders; its
# files landed loose in the download folder while the Web UI's went into
# Artist/Album folders.


def test_a_queued_job_uses_artist_and_album_folders_like_the_settings_page():
    config = queued_url_config({}, "/app/downloads/a", {})

    assert config.output.artist_subfolders is True
    assert config.output.album_subfolders is True
    assert config.output.playlist_subfolders is True
    assert config.output.filename_format == "{title} - {artist}"


def test_a_queued_job_follows_the_saved_folder_settings():
    saved = {
        "use_artist_subfolders": False,
        "use_album_subfolders": True,
        "create_playlist_subfolders": False,
        "filename_format": "{track_number} {title}",
        "acoustid_api_key": "not-copied",
    }

    config = queued_url_config({}, "/app/downloads/a", saved)

    assert config.output.artist_subfolders is False
    assert config.output.album_subfolders is True
    assert config.output.playlist_subfolders is False
    assert config.output.filename_format == "{track_number} {title}"


def test_single_user_rest_download_uses_the_saved_folder_settings():
    client, seen = _single_user_client("/srv/music", {"use_artist_subfolders": False})

    client.post("/api/v1/downloads", json={"url": "https://open.spotify.com/track/x"})

    assert seen["output"].artist_subfolders is False
    assert seen["output"].album_subfolders is True


def test_the_adapter_applies_the_layout_it_is_given():
    captured: dict = {}

    class FakeJobs:
        async def enqueue(self, request):
            captured["output"] = request.config.output
            return {"id": "j1", "status": "QUEUED"}

        def items(self, _job_id):
            return []

    import asyncio

    asyncio.run(
        ApiAdapter(job_service=FakeJobs()).submit_download(
            {
                "url": "https://open.spotify.com/track/x",
                "output_layout": {"use_album_subfolders": False},
            }
        )
    )

    assert captured["output"].artist_subfolders is True
    assert captured["output"].album_subfolders is False
