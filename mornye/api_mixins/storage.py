"""api_mixins/storage.py — Settings › Storage: what Mornye keeps on disk and
holds in memory, and clearing the parts that are regenerable
(core/storage_usage.py).

Over `--web` the report is readable by anyone signed in; clearing and
compacting are admin-only in multi-user mode (webapp.STORAGE_ADMIN_METHODS),
since one install's caches serve every account.
"""

from __future__ import annotations

from typing import Any


class StorageMixin:
    def storage_usage(self) -> dict[str, Any]:
        from ..core import storage_usage

        try:
            return {"status": "ok", **storage_usage.report()}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def storage_clear(self, item_id: str = "all") -> dict[str, Any]:
        from ..core import storage_usage

        try:
            return {"status": "ok", **storage_usage.clear(str(item_id or "all"))}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def storage_compact(self) -> dict[str, Any]:
        from ..core import storage_usage

        try:
            return {"status": "ok", **storage_usage.compact()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
