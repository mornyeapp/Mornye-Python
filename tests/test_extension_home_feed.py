"""Home feeds from extensions in Explore (Jellyfin, J3).

Explore only showed Spotify. Extensions with ``capabilities.homeFeed`` answer
``getHomeFeed()`` in the same shape; what they lack is a link the URL box
can open, which ``open_url`` supplies.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mornye.core import extension_home_feed as HF
from mornye.core.extension_enrichment import close_extension_provider

JELLYFIN = {
    "capabilities": {"homeFeed": True},
    "urlHandler": {"enabled": True, "patterns": ["#/details?id=", "#!/details?id="]},
    "settings": [
        {"key": "server_url", "type": "string"},
        {"key": "password", "type": "string", "secret": True},
    ],
}
APPLE = {
    "capabilities": {
        "homeFeed": True,
        "shareUrlTemplates": {"album": "https://music.apple.com/us/album/{id}"},
    }
}


def _ext(name, manifest):
    return SimpleNamespace(
        name=name, display_name=name.title(), manifest=manifest, runtime="javascript"
    )


def test_only_extensions_declaring_a_home_feed_are_sources() -> None:
    manager = SimpleNamespace(
        list_installed=lambda: [
            _ext("jellyfin", JELLYFIN),
            _ext("tidal-web", {"capabilities": {"relatedTracks": True}}),
            _ext("soundcloud", {"capabilities": {"homeFeed": False}}),
        ]
    )
    assert HF.home_feed_extensions(manager) == [
        {"id": "ext:jellyfin", "name": "jellyfin", "label": "Jellyfin"}
    ]


@pytest.mark.parametrize(
    ("item", "manifest", "settings", "expected"),
    [
        (  # a web link on the item wins
            {"id": "1", "type": "album", "external_url": "https://x.test/a/1"},
            APPLE,
            {},
            "https://x.test/a/1",
        ),
        (  # else the manifest's share template
            {"id": "42", "type": "album", "uri": "apple:album:42"},
            APPLE,
            {},
            "https://music.apple.com/us/album/42",
        ),
        (  # else, self-hosted: the configured server plus the fragment
            {"id": "abc", "type": "album", "uri": "jellyfin:album:abc"},
            JELLYFIN,
            {"server_url": "http://192.168.1.20:8096/", "password": "http://no"},
            "http://192.168.1.20:8096/web/#/details?id=abc",
        ),
        (  # else the uri as it is (spotify:… is understood)
            {"id": "s", "type": "album", "uri": "spotify:album:s"},
            {},
            {},
            "spotify:album:s",
        ),
    ],
)
def test_every_item_gets_a_link_the_url_box_can_open(
    item, manifest, settings, expected
) -> None:
    assert HF._open_url(item, manifest, settings) == expected


def test_the_jellyfin_link_is_one_its_url_handler_claims() -> None:
    from mornye.extensions.url_match import url_pattern_matches

    url = HF._open_url(
        {"id": "abc", "type": "album"}, JELLYFIN, {"server_url": "http://nas:8096"}
    )
    assert url_pattern_matches("#/details?id=", url)


def test_failures_and_empty_feeds_are_reported_not_raised() -> None:
    assert HF.normalise_feed(None, {}, {})["success"] is False
    failed = HF.normalise_feed({"success": False, "error": "login"}, {}, {})
    assert failed == {"success": False, "error": "login"}
    assert HF.normalise_feed({"sections": [{"items": []}]}, {}, {})["success"] is False


def test_a_feed_is_fetched_and_normalised() -> None:
    class Provider:
        def _call(self, method, *args):
            assert method == "getHomeFeed"
            return {
                "success": True,
                "greeting": "Good evening",
                "sections": [
                    {
                        "title": "Recently added",
                        "items": [
                            {"id": "abc", "type": "album", "name": "Album"},
                            {"id": "nameless"},
                        ],
                    }
                ],
            }

    manager = SimpleNamespace(
        list_installed=lambda: [_ext("jf-feed", JELLYFIN)],
        load_settings=lambda name: {"server_url": "http://nas:8096"},
    )
    feed = HF.fetch_home_feed(
        "ext:jf-feed", manager=manager, provider_factory=lambda name: Provider()
    )
    close_extension_provider("jf-feed")
    assert feed["success"] is True and feed["greeting"] == "Good evening"
    [section] = feed["sections"]
    assert [i["name"] for i in section["items"]] == ["Album"]
    assert section["items"][0]["open_url"] == "http://nas:8096/web/#/details?id=abc"


def test_an_unknown_source_is_an_error() -> None:
    manager = SimpleNamespace(list_installed=lambda: [])
    assert HF.fetch_home_feed("ext:nope", manager=manager)["success"] is False


def test_the_web_frontend_can_reach_both_calls() -> None:
    webapp = pytest.importorskip("mornye.webapp")
    assert {
        "get_home_feed_sources",
        "get_extension_home_feed",
    } <= webapp.ALLOWED_METHODS
