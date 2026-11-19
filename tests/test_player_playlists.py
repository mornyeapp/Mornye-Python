from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from mornye.api_mixins.player_playlists import PlayerPlaylistsMixin
from mornye.app import MornyeAPI
from mornye.core import player_playlists
from mornye.webapp import ALLOWED_METHODS


class _PlaylistApi(PlayerPlaylistsMixin):
    owner = "alice"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _is_approved_path(self, path: str) -> bool:
        try:
            return Path(path).resolve().is_relative_to(self.root)
        except OSError:
            return False


def _track(path: Path, title: str = "Song", artist: str = "Artist") -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return {
        "path": str(path),
        "title": title,
        "artist": artist,
        "album": "Album",
        "duration_s": 180,
    }


def test_playlist_crud_preserves_order_and_is_owner_scoped(tmp_path: Path) -> None:
    api = _PlaylistApi(tmp_path)
    first = _track(tmp_path / "01.flac", "First")
    second = _track(tmp_path / "02.flac", "Second")

    created = api.create_player_playlist(
        "Road trip", "A description", "", [first, second]
    )
    assert created["status"] == "ok"
    playlist = created["playlist"]
    assert [track["title"] for track in playlist["tracks"]] == ["First", "Second"]
    assert api.get_player_playlists()["playlists"][0]["description"] == "A description"

    added = api.add_player_playlist_tracks(playlist["id"], [first])
    assert [track["title"] for track in added["playlist"]["tracks"]] == [
        "First",
        "Second",
    ]
    reordered = api.reorder_player_playlist_tracks(
        playlist["id"], [second["path"], first["path"]]
    )
    assert [track["title"] for track in reordered["playlist"]["tracks"]] == [
        "Second",
        "First",
    ]
    exported = api.export_player_playlist(playlist["id"])
    assert exported["status"] == "ok"
    assert exported["text"].splitlines()[-1] == Path(first["path"]).as_uri()
    removed = api.remove_player_playlist_track(playlist["id"], first["path"])
    assert [track["title"] for track in removed["playlist"]["tracks"]] == ["Second"]

    other_owner = player_playlists.list_playlists("bob")
    assert other_owner == []
    assert api.delete_player_playlist("not-owned")["status"] == "error"
    assert api.delete_player_playlist(playlist["id"])["status"] == "ok"
    assert player_playlists.list_playlists("alice") == []


def test_playlist_cover_is_validated_and_stored_as_small_jpeg(tmp_path: Path) -> None:
    api = _PlaylistApi(tmp_path)
    image = Image.new("RGB", (1200, 900), "navy")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    created = api.create_player_playlist("Cover test", "Description", data_url, [])
    assert created["status"] == "ok"
    cover = created["playlist"]["cover"]
    assert cover.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(cover.partition(",")[2])
    with Image.open(io.BytesIO(raw)) as result:
        assert result.format == "JPEG"
        assert result.size == (512, 384)

    bad = api.create_player_playlist(
        "Bad cover", "", "data:image/png;base64,not-base64"
    )
    assert bad["status"] == "error"


def test_import_m3u_matches_local_files_and_reports_missing_tracks(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    local = _track(tmp_path / "music" / "song.flac", "Song", "Artist")
    text = (
        "#EXTM3U\n"
        "#EXTINF:180,Artist - Song\n"
        "song.flac\n"
        "#EXTINF:200,Missing - Track\n"
        "missing.flac\n"
    )

    preview = api.preview_player_playlist_import(text, "mix.m3u8", [local])
    assert preview == {
        "status": "ok",
        "matched": 1,
        "skipped": 1,
        "ambiguous": 0,
        "missing_tracks": ["Missing - Track"],
        "ambiguous_tracks": [],
    }
    assert player_playlists.list_playlists("alice") == []

    result = api.import_player_playlist(
        text, "mix.m3u8", "Mix", "Imported description", "", [local]
    )
    assert result["status"] == "ok"
    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert [track["title"] for track in result["playlist"]["tracks"]] == ["Song"]
    merged = api.import_player_playlist(
        text, "mix.m3u8", "Ignored", "", "", [local], result["playlist"]["id"]
    )
    assert merged["status"] == "ok"
    assert merged["imported"] == 0
    assert len(merged["playlist"]["tracks"]) == 1


def test_import_m3u_reports_ambiguous_duplicate_filenames(tmp_path: Path) -> None:
    api = _PlaylistApi(tmp_path)
    one = _track(tmp_path / "one" / "song.flac", "Song", "Artist")
    two = _track(tmp_path / "two" / "song.flac", "Song", "Artist")

    result = api.import_player_playlist(
        "#EXTM3U\n#EXTINF:180,Artist - Song\nsong.flac\n",
        "mix.m3u",
        "Mix",
        library_tracks=[one, two],
    )
    assert result["status"] == "ok"
    assert result["imported"] == 0
    assert result["ambiguous"] == 1


def test_import_m3u_matches_external_playlist_tracks_by_metadata(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    local = _track(tmp_path / "song.flac", "Song", "Artist")

    result = api.import_player_playlist(
        "#EXTM3U\n#EXTINF:180,Artist - Song\nhttps://open.spotify.com/track/id\n",
        "provider.m3u8",
        "Provider playlist",
        library_tracks=[local],
    )

    assert result["status"] == "ok"
    assert result["imported"] == 1
    assert [track["title"] for track in result["playlist"]["tracks"]] == ["Song"]


def test_fetch_playlist_search_import_returns_every_provider_track(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    import mornye.core.tracklist as tracklist_module

    async def resolve(_url):
        return SimpleNamespace(
            name="Provider mix",
            tracks=[
                SimpleNamespace(
                    title="Song",
                    artists="Artist",
                    duration_ms=180_000,
                    external_url="https://open.spotify.com/track/track-id",
                )
            ],
        )

    monkeypatch.setattr(tracklist_module, "resolve_tracklist", resolve)

    result = _PlaylistApi(tmp_path).fetch_player_playlist_search_import(
        "https://open.spotify.com/playlist/playlist-id"
    )

    assert result == {
        "status": "ok",
        "name": "Provider mix",
        "description": "",
        "cover": "",
        "tracks": [
            {
                "title": "Song",
                "artists": "Artist",
                "album": "",
                "duration_ms": 180_000,
                "isrc": "",
                "external_url": "https://open.spotify.com/track/track-id",
            }
        ],
    }


def test_fetch_playlist_import_downloads_a_public_m3u_link(
    tmp_path: Path, monkeypatch
) -> None:
    import mornye.api_mixins.player_playlists as playlist_api

    text = "#EXTM3U\n#EXTINF:180,Artist - Song\nhttps://audio.example/song\n"

    class _Response:
        status_code = 200
        headers = {"content-length": str(len(text.encode()))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield text.encode()

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, extensions):
            assert method == "GET"
            # The checked address, not the hostname: no second DNS lookup.
            assert url == "https://93.184.216.34/mix.m3u8?source=test"
            assert headers["Host"] == "playlists.example"
            assert extensions == {"sni_hostname": "playlists.example"}
            assert "audio/x-mpegurl" in headers["Accept"]
            return _Response()

    monkeypatch.setattr(playlist_api.httpx, "Client", _Client)
    monkeypatch.setattr(
        playlist_api.socket,
        "getaddrinfo",
        lambda host, port, type: [(None, None, None, None, ("93.184.216.34", port))],
    )

    result = _PlaylistApi(tmp_path).fetch_player_playlist_import(
        "https://playlists.example/mix.m3u8?source=test"
    )

    assert result == {
        "status": "ok",
        "text": text,
        "filename": "mix.m3u8",
    }


def test_fetch_playlist_import_rejects_non_public_hosts(
    tmp_path: Path, monkeypatch
) -> None:
    import mornye.api_mixins.player_playlists as playlist_api

    monkeypatch.setattr(
        playlist_api.socket,
        "getaddrinfo",
        lambda host, port, type: [(None, None, None, None, ("127.0.0.1", port))],
    )

    result = _PlaylistApi(tmp_path).fetch_player_playlist_import(
        "http://localhost/private.m3u"
    )

    assert result["status"] == "error"
    assert "publicly reachable" in result["error"]


def test_fetch_playlist_import_rejects_redirects_to_non_public_hosts(
    tmp_path: Path, monkeypatch
) -> None:
    import mornye.api_mixins.player_playlists as playlist_api

    requested_urls = []

    class _Response:
        status_code = 302
        headers = {"location": "http://127.0.0.1/private.m3u"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, _method, url, **_kwargs):
            requested_urls.append(url)
            return _Response()

    monkeypatch.setattr(playlist_api.httpx, "Client", _Client)
    monkeypatch.setattr(
        playlist_api.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (
                None,
                None,
                None,
                None,
                ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", port),
            )
        ],
    )

    result = _PlaylistApi(tmp_path).fetch_player_playlist_import(
        "https://playlists.example/mix.m3u"
    )

    assert result["status"] == "error"
    assert "publicly reachable" in result["error"]
    assert requested_urls == ["https://93.184.216.34/mix.m3u"]


def test_playlist_methods_are_available_over_both_api_surfaces() -> None:
    methods = (
        "get_player_playlists",
        "create_player_playlist",
        "update_player_playlist",
        "delete_player_playlist",
        "add_player_playlist_tracks",
        "remove_player_playlist_track",
        "reorder_player_playlist_tracks",
        "export_player_playlist",
        "fetch_player_playlist_import",
        "fetch_player_playlist_search_import",
        "preview_player_playlist_import",
        "import_player_playlist",
        "import_player_playlist_collection",
        "link_player_playlist_tracks",
    )
    assert all(name in ALLOWED_METHODS for name in methods)
    assert all(hasattr(MornyeAPI, name) for name in methods)
    web_shim = (
        Path(__file__).resolve().parents[1] / "mornye" / "frontend" / "web-shim.js"
    )
    assert all(f"'{name}'" in web_shim.read_text(encoding="utf-8") for name in methods)


def test_editing_a_playlist_keeps_the_saved_cover_bytes(tmp_path: Path) -> None:
    api = _PlaylistApi(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(image, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    created = api.create_player_playlist("Cover", "", data_url)["playlist"]

    updated = api.update_player_playlist(
        created["id"], "Renamed", "", created["cover"]
    )["playlist"]

    assert updated["name"] == "Renamed"
    assert updated["cover"] == created["cover"]


def _provider(title: str, artists: str, isrc: str = "", seconds: int = 180) -> dict:
    return {
        "title": title,
        "artists": artists,
        "album": "Daydream",
        "duration_ms": seconds * 1000,
        "isrc": isrc,
        "external_url": f"https://open.spotify.com/track/{title.replace(' ', '')}",
    }


def test_collection_import_keeps_every_song_and_marks_missing_ones(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    by_isrc = {
        **_track(tmp_path / "a.flac", "Fantasy", "Mariah Carey"),
        "isrc": "USSM19500001",
    }
    by_name = _track(
        tmp_path / "b.flac", "Always Be My Baby (feat. Someone)", "Mariah Carey"
    )

    result = api.import_player_playlist_collection(
        "Daydream mix",
        "Songs from 1995",
        "",
        [
            _provider("Fantasy (Remix)", "Mariah Carey", isrc="ussm19500001"),
            _provider("Underneath the Stars", "Mariah Carey"),
            _provider("Always Be My Baby", "Mariah Carey"),
        ],
        [by_isrc, by_name],
    )

    assert result["status"] == "ok", result
    assert (result["imported"], result["downloaded"], result["not_downloaded"]) == (
        3,
        2,
        1,
    )
    playlist = result["playlist"]
    assert playlist["name"] == "Daydream mix"
    assert playlist["description"] == "Songs from 1995"
    titles = [(t["title"], t["downloaded"]) for t in playlist["tracks"]]
    # Order of the source playlist, the file's own tags where there is one.
    assert titles == [
        ("Fantasy", True),
        ("Underneath the Stars", False),
        ("Always Be My Baby (feat. Someone)", True),
    ]
    missing = playlist["tracks"][1]
    assert missing["path"].startswith(player_playlists.REMOTE_PREFIX)
    assert missing["source_url"] == "https://open.spotify.com/track/UnderneaththeStars"


def test_collection_import_does_not_match_another_version_by_title(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    live = {
        **_track(tmp_path / "live.flac", "Fantasy", "Mariah Carey"),
        "duration_s": 420,
    }

    result = api.import_player_playlist_collection(
        "Mix", "", "", [_provider("Fantasy", "Mariah Carey", seconds=244)], [live]
    )

    assert result["not_downloaded"] == 1


def test_collection_import_copies_the_cover(tmp_path: Path, monkeypatch) -> None:
    import mornye.api_mixins.player_playlists as playlist_api

    image = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(image, format="PNG")
    fetched: list[str] = []

    def fake_fetch(url, accept, max_bytes):
        fetched.append(url)
        assert accept == "image/*"
        return image.getvalue(), url

    monkeypatch.setattr(playlist_api, "_fetch_public", fake_fetch)

    result = _PlaylistApi(tmp_path).import_player_playlist_collection(
        "Mix", "", "https://i.scdn.co/image/abc", [_provider("Song", "Artist")], []
    )

    assert fetched == ["https://i.scdn.co/image/abc"]
    assert result["playlist"]["cover"].startswith("data:image/jpeg;base64,")


def test_collection_import_survives_a_cover_that_cannot_be_fetched(
    tmp_path: Path, monkeypatch
) -> None:
    import mornye.api_mixins.player_playlists as playlist_api

    def refuse(*_args):
        raise ValueError("Only publicly reachable playlist links are supported.")

    monkeypatch.setattr(playlist_api, "_fetch_public", refuse)

    result = _PlaylistApi(tmp_path).import_player_playlist_collection(
        "Mix", "", "https://cover.example/x.jpg", [_provider("Song", "Artist")], []
    )

    assert result["status"] == "ok"
    assert result["playlist"]["cover"] == ""


def test_downloaded_songs_are_linked_to_their_file_later(tmp_path: Path) -> None:
    api = _PlaylistApi(tmp_path)
    created = api.import_player_playlist_collection(
        "Mix",
        "",
        "",
        [_provider("Song A", "Artist", isrc="AAA111"), _provider("Song B", "Artist")],
        [],
    )["playlist"]
    key_a, key_b = (t["path"] for t in created["tracks"])
    downloaded = _track(tmp_path / "song-a.flac", "Song A", "Artist")

    result = api.link_player_playlist_tracks(
        [
            {"key": key_a, "track": downloaded},
            # Not a playlist key, and a file outside the library: both ignored.
            {"key": "/etc/passwd", "track": downloaded},
            {"key": key_b, "track": {"path": "/etc/hosts", "title": "x"}},
        ]
    )

    assert result["linked"] == 1
    tracks = player_playlists.get_playlist(created["id"], "alice")["tracks"]
    assert [(t["title"], t["downloaded"]) for t in tracks] == [
        ("Song A", True),
        ("Song B", False),
    ]
    assert tracks[0]["path"] == str((tmp_path / "song-a.flac").resolve())
    assert tracks[0]["isrc"] == "AAA111"  # kept from the provider


def test_linking_a_song_already_in_the_playlist_does_not_list_it_twice(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    local = _track(tmp_path / "song.flac", "Song", "Artist")
    playlist = api.create_player_playlist("Mix", "", "", [local])["playlist"]
    remote = api.import_player_playlist_collection(
        "",
        "",
        "",
        [_provider("Song", "Other artist", isrc="ZZZ999")],
        [],
        playlist["id"],
    )
    key = next(t["path"] for t in remote["playlist"]["tracks"] if not t["downloaded"])

    api.link_player_playlist_tracks([{"key": key, "track": local}])

    tracks = player_playlists.get_playlist(playlist["id"], "alice")["tracks"]
    assert [t["path"] for t in tracks] == [str((tmp_path / "song.flac").resolve())]


def test_not_downloaded_songs_can_be_removed_reordered_and_exported(
    tmp_path: Path,
) -> None:
    api = _PlaylistApi(tmp_path)
    local = _track(tmp_path / "local.flac", "Local", "Artist")
    playlist = api.import_player_playlist_collection(
        "Mix",
        "",
        "",
        [
            _provider("Remote One", "Artist"),
            _provider("Local", "Artist"),
            _provider("Gone", "Artist"),
        ],
        [local],
    )["playlist"]
    paths = [t["path"] for t in playlist["tracks"]]

    reordered = api.reorder_player_playlist_tracks(
        playlist["id"], list(reversed(paths))
    )
    assert [t["title"] for t in reordered["playlist"]["tracks"]] == [
        "Gone",
        "Local",
        "Remote One",
    ]

    removed = api.remove_player_playlist_track(playlist["id"], paths[2])
    assert [t["title"] for t in removed["playlist"]["tracks"]] == [
        "Local",
        "Remote One",
    ]

    text = api.export_player_playlist(playlist["id"])["text"]
    assert (tmp_path / "local.flac").resolve().as_uri() in text
    assert "https://open.spotify.com/track/RemoteOne" in text


def test_remote_keys_prefer_isrc_then_link_then_tags() -> None:
    assert player_playlists.remote_key({"isrc": "usab1"}) == "remote:isrc:USAB1"
    assert (
        player_playlists.remote_key({"source_url": "https://x/y"})
        == "remote:url:https://x/y"
    )
    tagged = player_playlists.remote_key({"title": "A", "artist": "B", "album": "C"})
    assert tagged.startswith("remote:meta:")
    assert tagged == player_playlists.remote_key(
        {"title": " a ", "artist": "b", "album": "c"}
    )
