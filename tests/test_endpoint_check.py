"""The Test buttons next to the self-hosted endpoints (core/endpoint_check.py)."""

from __future__ import annotations

import httpx
import pytest

from mornye.core.endpoint_check import check_custom_endpoint, normalize_base_url


def client(
    routes: dict[str, tuple[int, object]], seen: list | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        status, body = routes.get(request.url.path, (404, {"detail": "Not Found"}))
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (" http://127.0.0.1:8000/ ", "http://127.0.0.1:8000"),
        ("https://hifi.example.test/api/?x=1#y", "https://hifi.example.test/api"),
        ("ftp://hifi.example.test", None),
        ("https://user:pw@hifi.example.test", None),
        ("hifi.example.test", None),
        ("", None),
    ],
)
def test_only_plain_http_urls_are_probed(url, expected) -> None:
    assert normalize_base_url(url) == expected


def test_a_working_hifi_api_server_passes() -> None:
    seen: list = []
    http = client(
        {"/": (200, {"version": "2.6"}), "/info/": (200, {"data": {"id": 1}})}, seen
    )
    result = check_custom_endpoint("tidal", "http://192.168.1.10:8000/", client=http)
    assert result["ok"] is True
    assert "2.6" in result["message"]
    assert [str(r.url) for r in seen] == [
        "http://192.168.1.10:8000/",
        "http://192.168.1.10:8000/info/?id=144371283",
    ]


def test_hifi_api_up_but_signed_out_is_reported() -> None:
    http = client({"/": (200, {"version": "2.6"}), "/info/": (500, {"detail": "auth"})})
    result = check_custom_endpoint("tidal", "http://h.test", client=http)
    assert result["ok"] is False
    assert "track lookup failed" in result["message"]


def test_something_that_is_not_hifi_api_is_reported() -> None:
    http = client({"/": (200, {"hello": "world"})})
    result = check_custom_endpoint("tidal", "http://h.test", client=http)
    assert result["ok"] is False
    assert "not like a hifi-api server" in result["message"]


def test_a_working_qobuz_rest_api_server_passes_with_the_key() -> None:
    seen: list = []
    http = client(
        {
            "/": (
                200,
                {"status": "online", "version": "2.2.0", "api_key_required": True},
            ),
            "/me": (200, {"display": "Francesco", "plan": "Studio Premier"}),
        },
        seen,
    )
    result = check_custom_endpoint(
        "qobuz", "http://127.0.0.1:8000", " secret ", client=http
    )
    assert result["ok"] is True
    assert (
        result["message"]
        == "qobuz-rest-api 2.2.0 works. Signed in as Francesco (Studio Premier)."
    )
    assert seen[1].headers["X-API-Key"] == "secret"
    assert "X-API-Key" not in seen[0].headers


@pytest.mark.parametrize(
    ("key", "word"), [("", "is missing"), ("wrong", "was refused")]
)
def test_a_missing_or_wrong_qobuz_key_is_named(key, word) -> None:
    http = client(
        {"/": (200, {"status": "online", "version": "2.2.0"}), "/me": (401, {})}
    )
    result = check_custom_endpoint("qobuz", "http://q.test", key, client=http)
    assert result["ok"] is False
    assert word in result["message"]


def test_an_unreachable_server_and_bad_input_do_not_raise() -> None:
    down = client({"/": (0, httpx.ConnectError("refused"))})
    assert (
        "Could not reach"
        in check_custom_endpoint("qobuz", "http://q.test", client=down)["message"]
    )
    slow = client({"/": (0, httpx.ReadTimeout("slow"))})
    assert (
        "No answer"
        in check_custom_endpoint("tidal", "http://h.test", client=slow)["message"]
    )
    assert check_custom_endpoint("deezer", "http://d.test")["ok"] is False
    assert "http://" in check_custom_endpoint("tidal", "nope")["message"]


def test_the_api_falls_back_to_the_saved_endpoint_and_key(monkeypatch) -> None:
    from mornye.api_mixins import extension_settings

    class Manager:
        def get_installed(self, name):
            return object()

        def load_settings(self, name):
            return {
                "customEndpoint": "http://saved.test",
                "customEndpointApiKey": "saved-key",
            }

    calls = []
    monkeypatch.setattr(extension_settings, "_manager", lambda: Manager())
    monkeypatch.setattr(
        "mornye.core.endpoint_check.check_custom_endpoint",
        lambda service, url, api_key="": calls.append((service, url, api_key))
        or {"ok": True},
    )
    api = extension_settings.ExtensionSettingsMixin()
    api.test_custom_endpoint("qobuz")
    api.test_custom_endpoint("qobuz", "http://typed.test", "typed-key")
    api.test_custom_endpoint("tidal", "")
    assert calls == [
        ("qobuz", "http://saved.test", "saved-key"),
        ("qobuz", "http://typed.test", "typed-key"),
        ("tidal", "http://saved.test", ""),
    ]
    assert api.test_custom_endpoint("deezer")["ok"] is False


def test_the_test_is_exposed_to_the_web_frontend_for_admins_only() -> None:
    from mornye import webapp

    assert "test_custom_endpoint" in webapp.ALLOWED_METHODS
    assert "test_custom_endpoint" in webapp.EXTENSION_SETTINGS_ADMIN_METHODS
