"""Apple track search must not supply artwork from a different release."""

from __future__ import annotations

import asyncio

import pytest

from mornye.core.metadata_enrichment import _AppleMusicMeta


@pytest.mark.parametrize(
    ("album_name", "expected_cover"),
    [
        ("1962–1966 (2023 Edition)", ""),
        (
            "Past Masters",
            "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/foo/600x600bb.jpg",
        ),
        (
            "",
            "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/foo/600x600bb.jpg",
        ),
    ],
)
def test_itunes_artwork_is_only_used_for_the_requested_release(
    album_name: str,
    expected_cover: str,
) -> None:
    item = {
        "trackName": "I Want To Hold Your Hand - 2023 Mix",
        "artistName": "The Beatles",
        "collectionName": "Past Masters",
        "artworkUrl100": (
            "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/foo/100x100bb.jpg"
        ),
        "primaryGenreName": "Rock",
    }

    async def search(*_args):
        return item

    apple = _AppleMusicMeta()
    apple._search_async = search
    result = asyncio.run(
        apple.fetch_async(
            "I Want To Hold Your Hand - 2023 Mix",
            "The Beatles",
            album_name=album_name,
        )
    )

    assert result.cover_url_hd == expected_cover
    assert result.genre == "Rock"
