"""urlHandler patterns are anchored to the URL's host (extensions/url_match.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mornye.core.extension_metadata import handles_url
from mornye.core.metadata_fallback import fallback_extension_for
from mornye.extensions.url_match import handler_matches, url_pattern_matches


@pytest.mark.parametrize(
    ("pattern", "url", "expected"),
    [
        ("tidal.com", "https://tidal.com/browse/track/1", True),
        ("tidal.com", "https://listen.tidal.com/album/1", True),
        ("TIDAL.com", "https://Tidal.COM/x", True),
        ("tidal.com", "https://nottidal.com/x", False),
        ("tidal.com", "https://example.org/?next=tidal.com", False),
        ("tidal.com", "https://tidal.com.evil.example/x", False),
        ("tidal.com/browse", "https://tidal.com/browse/x", True),
        ("tidal.com/browse", "https://tidal.com/album/x", False),
        ("https://music.apple.com/", "https://music.apple.com/us/album/1", True),
        ("open.spotify.com", "open.spotify.com/track/abc", True),
        ("spotify:", "spotify:track:abc", True),
        ("spotify:", "https://evil.example/spotify:", False),
        (r"soundcloud\.com", "https://soundcloud.com/a/b", True),
        (r"soundcloud\.com", "https://m.soundcloud.com/a", True),
        (r"soundcloud\.com", "https://notsoundcloud.com/a", False),
        (r"soundcloud\.com", "https://evil.example/soundcloud.com", False),
        (r"soundcloud\.com", "https://soundcloud.com.evil.example/", False),
        (r"^https?://(www\.)?deezer\.com/", "https://www.deezer.com/track/1", True),
        (r"^https?://(www\.)?deezer\.com/", "https://deezer.com.evil.io/", False),
        (r"deezer\.(com|page\.link)", "https://deezer.page.link/xyz", True),
        ("[invalid", "https://x.com", False),
        ("", "https://x.com", False),
    ],
)
def test_pattern(pattern, url, expected):
    assert url_pattern_matches(pattern, url) is expected


def _manifest(enabled=None, patterns=("tidal.com",)):
    handler = {"patterns": list(patterns)}
    if enabled is not None:
        handler["enabled"] = enabled
    return {"urlHandler": handler, "type": ["metadata_provider"]}


def test_enabled_semantics():
    url = "https://tidal.com/track/1"
    assert handler_matches(_manifest(), url)
    assert handler_matches(_manifest(True), url)
    assert not handler_matches(_manifest(False), url)
    assert not handler_matches(_manifest(), url, require_enabled=True)
    assert not handler_matches({"urlHandler": "nope"}, url)


class _Manager:
    def __init__(self, exts):
        self._exts = exts

    def list_installed(self):
        return self._exts


def _ext(name, patterns, enabled=True):
    manifest = _manifest(enabled, patterns)
    return SimpleNamespace(
        name=name,
        manifest=manifest,
        types=manifest["type"],
        url_patterns=list(patterns),
    )


def test_broad_pattern_no_longer_steals_links():
    # "aaa" sorts first; its "tidal" pattern used to claim every URL with
    # that word anywhere in it.
    mgr = _Manager([_ext("aaa", ["tidal"]), _ext("tidal", ["tidal.com"])])
    assert fallback_extension_for("https://tidal.com/browse/track/1", mgr) == "tidal"


def test_handles_url_requires_enabled():
    assert handles_url(_ext("x", [r"soundcloud\.com"]), "https://soundcloud.com/a")
    assert not handles_url(
        _ext("x", [r"soundcloud\.com"], enabled=None), "https://soundcloud.com/a"
    )


def test_a_fragment_pattern_matches_a_self_hosted_app_on_any_host():
    """Jellyfin's `#/details?id=`: no host to anchor to, so the fragment is
    the anchor — and the `?` in it is a literal, not a regex quantifier."""
    from mornye.extensions.url_match import url_pattern_matches

    item = "details?id=0123456789abcdef0123456789abcdef"
    assert url_pattern_matches(
        "#/details?id=", f"http://192.168.1.10:8096/web/#/{item}"
    )
    assert url_pattern_matches(
        "#!/details?id=", f"https://jf.example.com/web/#!/{item}"
    )
    # In the query or path, or later in the fragment, is not the fragment.
    assert not url_pattern_matches(
        "#/details?id=", "https://example.org/?x=%23/details?id=1"
    )
    assert not url_pattern_matches(
        "#/details?id=", "https://example.org/#/other/#/details?id=1"
    )
    assert not url_pattern_matches("#/details?id=", "https://example.org/details?id=1")
