"""errors.log keeps the tracebacks the console leaves out (ERROR only)."""

from __future__ import annotations

import logging

import pytest

from mornye.core import error_log


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MORNYE_CACHE_DIR", str(tmp_path))
    return tmp_path


def _log_through(handler):
    logger = logging.getLogger("mornye.test_error_log")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.warning("an expected provider failure")
        try:
            raise TimeoutError()
        except TimeoutError:
            logger.exception("[worker] Unexpected exception downloading '%s'", "Song")
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_an_unexpected_error_is_written_with_its_traceback(cache):
    handler = error_log.error_log_handler()
    assert handler is not None
    _log_through(handler)

    text = error_log.error_log_path().read_text(encoding="utf-8")
    assert error_log.error_log_path().parent == cache
    assert "[worker] Unexpected exception downloading 'Song'" in text
    assert "Traceback (most recent call last)" in text
    assert "TimeoutError" in text


def test_expected_failures_below_error_stay_out(cache):
    handler = error_log.error_log_handler()
    _log_through(handler)

    assert "an expected provider failure" not in error_log.error_log_path().read_text(
        encoding="utf-8"
    )


def test_a_folder_that_cannot_be_written_gives_no_handler(cache, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(error_log.Path, "mkdir", refuse)
    assert error_log.error_log_handler() is None


def test_the_gui_logging_installs_it(cache):
    from logging.handlers import RotatingFileHandler

    from mornye.app import configure_console_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_console_logging(logging.INFO)
        files = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(files) == 1
        assert files[0].baseFilename == str(error_log.error_log_path())
        assert files[0].level == logging.ERROR
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
