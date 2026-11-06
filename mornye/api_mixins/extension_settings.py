"""api_mixins/extension_settings.py — each JS extension's own settings.

Every Mornye-Extension manifest declares the settings its extension reads
(``settings``: key, type, label, default, options…) and the extension
receives them in ``initialize(settings)`` when its Node process starts.
Mornye stored them in ``<ext_dir>/<name>/settings.json`` but offered no way
to change them; this is that way, for the GUI and the web frontend.

Secret values (``"secret": true`` in the manifest, e.g. a manual PO token)
never travel back to the frontend: they are reported as set or not set, and
saving an empty secret leaves the stored one alone. "Reset" clears them.
"""

from __future__ import annotations

from typing import Any


def _manager() -> Any:
    from ..extensions.manager import ExtensionManager

    return ExtensionManager(auto_install_downloads=False)


def _forget_running(ext_name: str) -> None:
    """Make the next call start the extension with its new settings."""
    try:
        from ..core.extension_enrichment import close_extension_provider

        close_extension_provider(ext_name)
    except Exception:
        pass


def _describe(manager: Any, ext: Any) -> dict:
    schema = ext.settings_schema
    values = manager.load_settings(ext.name)
    public_values: dict[str, Any] = {}
    secrets_set: list[str] = []
    for entry in schema:
        key = entry["key"]
        if entry["type"] == "button":
            continue
        value = values.get(key, entry.get("default"))
        if entry.get("secret"):
            if value not in ("", None):
                secrets_set.append(key)
            public_values[key] = ""
        else:
            public_values[key] = value
    return {
        "name": ext.name,
        "display_name": ext.display_name or ext.name,
        "version": ext.version,
        "runtime": ext.runtime,
        "types": list(ext.types),
        "settings": schema,
        "values": public_values,
        "secrets_set": secrets_set,
        "quality_options": ext.quality_options,
    }


class ExtensionSettingsMixin:
    def list_extension_settings(self) -> dict:
        """Every installed JS extension that declares settings.

        Shape: {"extensions": [{"name", "display_name", "version",
        "runtime", "types", "settings", "values", "secrets_set",
        "quality_options"}, ...]}, sorted by display name.
        """
        try:
            manager = _manager()
            rows = [
                _describe(manager, ext)
                for ext in manager.list_installed()
                if ext.runtime != "python" and ext.settings_schema
            ]
        except Exception as e:
            return {"extensions": [], "error": str(e)}
        rows.sort(key=lambda row: str(row["display_name"]).lower())
        return {"extensions": rows}

    def get_extension_settings(self, name: str) -> dict:
        try:
            manager = _manager()
            ext = manager.get_installed(str(name or ""))
            if ext is None:
                return {"error": f"Extension '{name}' is not installed."}
            return _describe(manager, ext)
        except Exception as e:
            return {"error": str(e)}

    def save_extension_settings(self, name: str, values: dict) -> dict:
        """Validate and store settings; the extension restarts with them."""
        try:
            manager = _manager()
            ext = manager.get_installed(str(name or ""))
            if ext is None:
                return {"error": f"Extension '{name}' is not installed."}
            if not isinstance(values, dict):
                return {"error": "Settings must be an object of key/value pairs."}
            secret_keys = {e["key"] for e in ext.settings_schema if e.get("secret")}
            incoming = {
                key: value
                for key, value in values.items()
                # An empty secret means "unchanged": the frontend never had it.
                if not (key in secret_keys and value in ("", None))
            }
            manager.update_settings(ext.name, incoming)
            _forget_running(ext.name)
            return {"ok": True, **_describe(manager, ext)}
        except Exception as e:
            return {"error": str(e)}

    def reset_extension_settings(self, name: str) -> dict:
        try:
            manager = _manager()
            ext = manager.get_installed(str(name or ""))
            if ext is None:
                return {"error": f"Extension '{name}' is not installed."}
            manager.reset_settings(ext.name)
            _forget_running(ext.name)
            return {"ok": True, **_describe(manager, ext)}
        except Exception as e:
            return {"error": str(e)}

    def run_extension_setting_action(self, name: str, key: str) -> dict:
        """Run a ``button`` setting: the function its manifest names.

        Only the action declared for that key can be called, so this cannot
        reach any other function of the extension.
        """
        try:
            manager = _manager()
            ext = manager.get_installed(str(name or ""))
            if ext is None:
                return {"error": f"Extension '{name}' is not installed."}
            entry = next(
                (e for e in ext.settings_schema if e["key"] == key),
                None,
            )
            if entry is None or entry["type"] != "button" or not entry.get("action"):
                return {"error": f"'{key}' is not an action of '{ext.name}'."}

            from ..extensions.provider import JSExtensionProvider

            provider = JSExtensionProvider(ext.name, timeout_s=60)
            try:
                result = provider._call(entry["action"])
            finally:
                provider.close()
            return {"ok": True, "result": result}
        except Exception as e:
            return {"error": str(e)}
