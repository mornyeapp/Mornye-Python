"""Backward-compatible downloader module.

The implementation is owned by ``mornye.application.download_engine``.
This module remains as a compatibility facade for existing imports and legacy
monkeypatch targets such as ``mornye.downloader.MornyeDownloader``.

class LegacyDownloadWorker is intentionally preserved as a compatibility name;
its implementation now lives in the application engine.
"""

from __future__ import annotations

import sys
import types
from collections import ChainMap
from typing import TYPE_CHECKING

from .application import download_engine as _engine

if TYPE_CHECKING:
    from .application.download_engine import (
        DownloadOptions as DownloadOptions,
        MornyeDownloader as MornyeDownloader,
        _call_metadata_get_url as _call_metadata_get_url,
    )


class _DownloaderCompatModule(types.ModuleType):
    """Proxy reads and writes to the application-owned download engine."""

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(_engine, name)

    def __setattr__(self, name: str, value) -> None:  # type: ignore[no-untyped-def]
        setattr(_engine, name, value)

    def __delattr__(self, name: str) -> None:
        delattr(_engine, name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_engine)))

    @property
    def __dict__(self):  # type: ignore[override]
        # Tools that undo an attribute patch (pytest >= 9's monkeypatch) look
        # the previous value up in ``target.__dict__`` rather than with
        # getattr().  Forwarded names live in the engine's namespace, not
        # here, so a bare module dict made them look "absent" and the undo
        # *deleted* the engine attribute instead of restoring it.  Reads
        # fall through to the engine; writes go to it, like __setattr__.
        return ChainMap(
            _engine.__dict__, types.ModuleType.__getattribute__(self, "__dict__")
        )


sys.modules[__name__].__class__ = _DownloaderCompatModule

__all__ = [name for name in dir(_engine) if not name.startswith("_")]
