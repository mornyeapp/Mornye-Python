"""A download service carries the logo its extension ships."""

from __future__ import annotations

from types import SimpleNamespace

from mornye.extensions import catalog


def _ext(tmp_path, icon, data=b"\x89PNG\r\n\x1a\nfake", name="icon.png"):
    d = tmp_path / "ext"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    return SimpleNamespace(manifest={"icon": icon}, ext_dir=d)


def test_a_shipped_logo_is_a_data_url(tmp_path):
    url = catalog.extension_icon_data_url(_ext(tmp_path, "icon.png"))
    assert url.startswith("data:image/png;base64,")


def test_no_icon_is_empty(tmp_path):
    assert (
        catalog.extension_icon_data_url(SimpleNamespace(manifest={}, ext_dir=tmp_path))
        == ""
    )


def test_a_path_outside_the_extension_is_refused(tmp_path):
    (tmp_path / "secret.png").write_bytes(b"x")
    ext = _ext(tmp_path, "../secret.png")
    assert catalog.extension_icon_data_url(ext) == ""


def test_a_non_image_or_oversized_file_is_refused(tmp_path):
    assert (
        catalog.extension_icon_data_url(_ext(tmp_path, "index.js", name="index.js"))
        == ""
    )
    big = b"x" * (catalog._ICON_MAX_BYTES + 1)
    assert (
        catalog.extension_icon_data_url(_ext(tmp_path, "big.png", big, "big.png")) == ""
    )
