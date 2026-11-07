"""HTTP(S) upload destinations, and the extensions' own health checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from mornye.core.http_destination import HTTPUploadBackend
from mornye.core.network_destinations import (
    DestinationError,
    DestinationStore,
    HTTPProfile,
    create_backend,
    validate_profile,
)


def _profile(**changes):
    values = {
        "type": "http",
        "id": "httpdest1234",
        "name": "NAS drop",
        "url": "https://nas.example.net/upload",
        "subdirectory": "Music",
        "username": "alice",
    }
    values.update(changes)
    return validate_profile(values)


def _backend(profile, handler, password="pw"):
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        auth=(profile.username, password) if profile.username else None,
    )
    return HTTPUploadBackend(profile, profile.username, password, client=client)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://nas.local:8080/upload",
        "https://nas.example.net/upload/",
        "https://nas.example.net/files/{path}/raw",
    ],
)
def test_http_and_https_upload_urls_are_accepted(url) -> None:
    profile = _profile(url=url)
    assert isinstance(profile, HTTPProfile)
    assert profile.method == "PUT"
    assert "{path}" in profile.url or not profile.url.endswith("/")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"url": "ftp://nas.local/upload"}, "HTTP or HTTPS"),
        ({"url": "https://nas.local/upload?token=x"}, "query"),
        ({"url": "https://bob:pw@nas.local/upload"}, "credentials"),
        ({"url": "https://nas.local/a/../b"}, "server path"),
        ({"method": "DELETE"}, "PUT or POST"),
        ({"method": "POST", "url": "https://nas.local/{path}"}, "PUT uploads"),
        ({"url": "https://nas.local/{path}/{path}"}, "at most once"),
        ({"subdirectory": "../etc"}, "subfolder"),
    ],
)
def test_bad_upload_profiles_are_rejected(changes, message) -> None:
    with pytest.raises(DestinationError, match=message):
        _profile(**changes)


def test_the_store_saves_and_reloads_an_http_destination(tmp_path) -> None:
    store = DestinationStore(tmp_path / "destinations.json")
    store.save({**_profile().public_dict(), "method": "POST"})
    [saved] = store.list()
    assert saved["type"] == "http"
    assert saved["method"] == "POST"
    backend = create_backend(store.get(saved["id"]), "", "")
    assert isinstance(backend, HTTPUploadBackend)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _staged(tmp_path):
    root = tmp_path / "stage"
    (root / "Artist" / "Album").mkdir(parents=True)
    (root / "Artist" / "Album" / "01 Song #1.flac").write_bytes(b"FLAC")
    return root


def test_put_appends_the_quoted_path_under_the_subfolder(tmp_path) -> None:
    seen = []

    def handler(request):
        seen.append(
            (request.method, str(request.url), request.content, request.headers)
        )
        return httpx.Response(201)

    delivered = _backend(_profile(), handler).upload_tree(_staged(tmp_path))
    method, url, body, headers = seen[0]
    assert method == "PUT"
    assert url == (
        "https://nas.example.net/upload/Music/Artist/Album/01%20Song%20%231.flac"
    )
    assert body == b"FLAC"
    assert headers["authorization"].startswith("Basic ")
    assert delivered == [url]


def test_put_fills_a_path_placeholder(tmp_path) -> None:
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200)

    profile = _profile(url="https://nas.example.net/files/{path}/raw", subdirectory="")
    _backend(profile, handler).upload_tree(_staged(tmp_path))
    assert seen == [
        "https://nas.example.net/files/Artist/Album/01%20Song%20%231.flac/raw"
    ]


def test_post_sends_a_form_with_the_file_and_its_path(tmp_path) -> None:
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.content))
        return httpx.Response(200)

    _backend(_profile(method="POST"), handler).upload_tree(_staged(tmp_path))
    method, url, body = seen[0]
    assert (method, url) == ("POST", "https://nas.example.net/upload")
    assert b'name="path"' in body and b"Music/Artist/Album/01 Song #1.flac" in body
    assert b'filename="01 Song #1.flac"' in body and b"FLAC" in body


def test_a_busy_server_is_retried_and_a_refusal_is_explained(tmp_path) -> None:
    answers = [httpx.Response(503), httpx.Response(201)]
    _backend(_profile(), lambda request: answers.pop(0)).upload_tree(_staged(tmp_path))
    assert answers == []

    with pytest.raises(DestinationError, match="refused the credentials"):
        _backend(_profile(), lambda request: httpx.Response(401)).upload_tree(
            _staged(tmp_path / "again")
        )


def test_the_connection_test_uploads_a_probe_and_removes_it() -> None:
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(204 if request.method == "DELETE" else 201)

    result = _backend(_profile(), handler).test_connection()
    assert result["ok"] is True
    assert seen == ["PUT", "DELETE"]


def test_an_upload_destination_is_not_a_library() -> None:
    with pytest.raises(DestinationError, match="cannot be browsed"):
        _backend(_profile(), lambda request: httpx.Response(200)).list_audio_files()


# ---------------------------------------------------------------------------
# Extension health (manifest `serviceHealth`)
# ---------------------------------------------------------------------------


def _ext(name, checks, display=None):
    return SimpleNamespace(
        name=name, display_name=display or name, manifest={"serviceHealth": checks}
    )


SHARED = "https://api.example.net/v1/health"


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    status_page = {
        "services": {
            "deezer": {"ok": True, "status": 200, "detail": None, "error": None},
            "pandora": {"ok": False, "status": None, "error": "fetch failed"},
        }
    }

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == SHARED:
            return httpx.Response(200, json=status_page)
        if "down" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("mornye.core.health_check.httpx.AsyncClient", client)
    return calls


def test_each_extension_is_judged_by_its_own_checks(fake_http) -> None:
    from mornye.core.health_check import run_extension_health_check

    manager = SimpleNamespace(
        list_installed=lambda: [
            _ext("deezer", [{"label": "Zarz", "url": SHARED, "serviceKey": "deezer"}]),
            _ext(
                "pandora", [{"label": "Zarz", "url": SHARED, "serviceKey": "pandora"}]
            ),
            _ext(
                "soundcloud",
                [
                    {"label": "Site", "url": "https://sc.example/"},
                    {
                        "label": "Mirror",
                        "url": "https://down.example/",
                        "required": False,
                    },
                ],
            ),
            _ext("no-checks", []),
            _ext("bad-scheme", [{"url": "file:///etc/passwd"}]),
        ]
    )
    rows = asyncio.run(run_extension_health_check(manager=manager))
    by_name = {row["name"]: row for row in rows}

    assert set(by_name) == {"deezer", "pandora", "soundcloud"}
    assert by_name["deezer"]["ok"] is True
    assert by_name["pandora"]["ok"] is False
    assert by_name["pandora"]["checks"][0]["detail"] == "fetch failed"
    # An optional check failing does not fail the extension.
    assert by_name["soundcloud"]["ok"] is True
    assert [c["ok"] for c in by_name["soundcloud"]["checks"]] == [True, False]
    # The shared status page is fetched once for both extensions.
    assert fake_http.count(SHARED) == 1


def test_listing_the_checks_fetches_nothing(fake_http) -> None:
    from mornye.core.health_check import run_extension_health_check

    manager = SimpleNamespace(
        list_installed=lambda: [
            _ext("deezer", [{"url": SHARED, "serviceKey": "deezer"}])
        ]
    )
    [row] = asyncio.run(run_extension_health_check(manager=manager, probe=False))
    assert row["ok"] is None and row["checks"][0]["ok"] is None
    assert fake_http == []


def test_the_check_is_reachable_from_the_frontend() -> None:
    from mornye.app import MornyeAPI
    from mornye.webapp import ALLOWED_METHODS

    assert "check_extension_health" in ALLOWED_METHODS
    assert hasattr(MornyeAPI, "check_extension_health")
