"""Settings › Storage (core/storage_usage.py): sizes what is on disk and
clears only regenerable caches."""

from __future__ import annotations

import pytest

from mornye.core import db, storage_usage


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MORNYE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MORNYE_CACHE_DIR", raising=False)
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "mornye.db"))
    yield tmp_path
    db.close_thread_connection()


def _by_id(report):
    return {item["id"]: item for item in report["disk"]}


def test_report_sizes_caches_and_memory(data_dir):
    (data_dir / "thumbs" / "ab").mkdir(parents=True)
    (data_dir / "thumbs" / "ab" / "x.jpg").write_bytes(b"x" * 1000)
    (data_dir / ".cache" / "responses").mkdir(parents=True)
    (data_dir / ".cache" / "responses" / "a.json").write_text("{}" * 50)

    report = storage_usage.report()
    items = _by_id(report)
    assert items["thumbs"]["bytes"] == 1000
    assert items["thumbs"]["clearable"]
    assert items["responses"]["bytes"] == 100
    assert not items["extensions"]["clearable"]
    assert items["database"]["compactable"]
    assert report["memory"]["total_bytes"] > 0


def test_clear_removes_only_the_named_cache(data_dir):
    (data_dir / "thumbs").mkdir()
    (data_dir / "thumbs" / "x.jpg").write_bytes(b"x" * 10)
    (data_dir / "extensions").mkdir()
    (data_dir / "extensions" / "keep.js").write_text("keep")

    result = storage_usage.clear("thumbs")
    assert result["cleared"] == ["thumbs"]
    assert result["freed_bytes"] == 10
    assert not (data_dir / "thumbs").exists()
    assert (data_dir / "extensions" / "keep.js").exists()


def test_clear_scan_results_empties_the_table(data_dir):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO local_scan_cache (path, mtime_ns, size, entry, scanned_at)"
            " VALUES ('/a.flac', 1, 1, '{}', 0)"
        )
    storage_usage.clear("local_scan")
    count = (
        db.connection().execute("SELECT COUNT(*) FROM local_scan_cache").fetchone()[0]
    )
    assert count == 0


def test_clear_refuses_what_is_not_a_cache(data_dir):
    with pytest.raises(ValueError):
        storage_usage.clear("extensions")
    with pytest.raises(ValueError):
        storage_usage.clear("database")
