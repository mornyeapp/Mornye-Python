"""A file that keeps the tracebacks the console leaves out.

The console drops tracebacks below DEBUG (client._CleanConsoleFormatter), so
that an expected provider failure is one clean line and not a page of
frames. That is right for the terminal, but it also meant an unexpected
error, logged with logger.exception(), left nothing to find afterwards: the
desktop app printed "Unexpected error:" with an empty message and the frames
that would have explained it were gone.

errors.log, in the cache folder, takes ERROR records only, with their full
traceback. Expected failures are logged at WARNING or below and stay out of
it, so it holds the few records worth reading. It rotates at 1 MB and keeps
two older files.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import cache_path

ERROR_LOG_NAME = "errors.log"
_MAX_BYTES = 1_000_000
_BACKUPS = 2
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def error_log_path() -> Path:
    return cache_path(ERROR_LOG_NAME)


def error_log_handler() -> logging.Handler | None:
    """The handler that writes errors.log, or None if it cannot be opened.

    Never raises: a read-only or full disk must not stop the app from
    starting, only from keeping this file.
    """
    try:
        path = error_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUPS,
            encoding="utf-8",
            delay=True,
        )
    except OSError:
        return None
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler
