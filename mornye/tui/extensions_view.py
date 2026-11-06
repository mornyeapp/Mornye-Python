"""extensions_view.py — Where providers come from.

An extension registry is a link to a list of installable providers. Nothing
downloads until at least one is configured, which makes this the panel a
fresh install visits first and, once it works, never again.

`extensions/registry_config` is the same module the wizard's registry menu
and the GUI's Settings → Extensions tab read, so a link added here shows up
in both. The three sources it distinguishes matter: a link exported in the
terminal or written into a `.env` file cannot be edited from a UI — it comes
back the moment the process restarts — and saying so is more useful than
offering a delete that will not stick.

Editing the list installs from it straight away, rather than at the next
launch, because the reason to add a registry is always the provider you
wanted a minute ago.
"""

from __future__ import annotations

import asyncio

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Select, Switch

#: How each source reads to someone who did not set it up.
SOURCE_LABELS = {
    "environment": "terminal export",
    "env_file": ".env file",
    "custom": "added here",
}

_COLUMNS = ("Registry", "Source", "State")


class ExtensionsPanel(VerticalScroll):
    """The configured registry links, with add and remove."""

    BORDER_TITLE = "Extensions"

    def __init__(self, min_trust_tier: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._min_trust_tier = min_trust_tier
        self._urls: list[str] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Label(
            "Registry links. Providers install from these; without one "
            "there is nothing to download.",
            classes="panel-intro",
        )
        table: DataTable = DataTable(id="registry-table", zebra_stripes=True)
        table.cursor_type = "row"
        yield table
        yield Horizontal(
            Input(placeholder="https://…/registry.json", id="registry-url"),
            Button("Add", id="registry-add", variant="primary"),
            Button("Remove selected", id="registry-remove", variant="warning"),
            classes="setting-row",
        )
        yield Label("", id="registry-status")

        # Each JS extension's own settings, from its manifest — the same
        # editor as the GUI's Settings → Extensions (both go through
        # api_mixins/extension_settings).
        yield Label("Extension settings", classes="panel-intro")
        yield Select([], prompt="Choose an extension…", id="extset-picker")
        yield Vertical(id="extset-form")
        yield Horizontal(
            Button("Save", id="extset-save", variant="primary"),
            Button("Reset to defaults", id="extset-reset", variant="warning"),
            classes="setting-row",
        )
        yield Label("", id="extset-status")

    def on_mount(self) -> None:
        table = self.query_one("#registry-table", DataTable)
        for column in _COLUMNS:
            table.add_column(column, key=column)
        self.run_worker(self.reload(), exclusive=False)
        self._extset: dict | None = None
        self._extset_widgets: dict[str, str] = {}
        self.call_after_refresh(
            lambda: self.run_worker(self._load_extension_list(), exclusive=False)
        )

    # ── Extension settings ────────────────────────────────────────────────

    @staticmethod
    def _settings_api():
        from ..api_mixins.extension_settings import ExtensionSettingsMixin

        return ExtensionSettingsMixin()

    def _extset_say(self, message: str) -> None:
        self.query_one("#extset-status", Label).update(message)

    async def _load_extension_list(self) -> None:
        data = await asyncio.to_thread(self._settings_api().list_extension_settings)
        rows = data.get("extensions") or []
        picker = self.query_one("#extset-picker", Select)
        picker.set_options(
            [(f"{r['display_name']} ({r['name']})", r["name"]) for r in rows]
        )
        if data.get("error"):
            self._extset_say(f"Could not read the extensions — {data['error']}")
        elif not rows:
            self._extset_say("No installed extension has settings.")
        else:
            self._extset_say(f"{len(rows)} extension(s) with settings.")

    async def _show_extension(self, name: str) -> None:
        data = await asyncio.to_thread(
            self._settings_api().get_extension_settings, name
        )
        form = self.query_one("#extset-form", Vertical)
        await form.remove_children()
        self._extset = None
        self._extset_widgets = {}
        if data.get("error"):
            self._extset_say(data["error"])
            return
        self._extset = data
        rows = []
        for idx, entry in enumerate(data["settings"]):
            key = entry["key"]
            widget_id = f"extset-f-{idx}-{re.sub(r'[^A-Za-z0-9_-]', '_', key)}"
            self._extset_widgets[widget_id] = key
            value = data["values"].get(key, entry.get("default"))
            widget: Input | Select | Switch | Button
            if entry["type"] == "button":
                widget = Button(entry["label"], id=widget_id)
            elif entry["type"] == "boolean":
                widget = Switch(value=bool(value), id=widget_id)
            elif entry["type"] == "select" and entry["options"]:
                options = [(o["label"], o["value"]) for o in entry["options"]]
                allowed = {v for _l, v in options}
                current = str(value) if str(value) in allowed else options[0][1]
                widget = Select(options, value=current, allow_blank=False, id=widget_id)
            elif entry.get("secret"):
                is_set = key in data.get("secrets_set", [])
                widget = Input(
                    password=True,
                    placeholder=(
                        "saved — leave empty to keep it" if is_set else "not set"
                    ),
                    id=widget_id,
                )
            else:
                widget = Input(
                    value="" if value is None else str(value),
                    placeholder=str(entry.get("default") or ""),
                    type="number" if entry["type"] == "number" else "text",
                    id=widget_id,
                )
            label = entry["label"]
            if entry.get("description"):
                label = f"{label} — {entry['description']}"
            rows.append(
                Horizontal(Label(label, markup=False), widget, classes="setting-row")
            )
        if rows:
            await form.mount_all(rows)
        self._extset_say(
            f"{data['display_name']}: {len(rows)} setting(s). Saved values apply "
            "the next time the extension starts."
        )

    def _collect_extension_values(self) -> dict:
        values: dict = {}
        if not self._extset:
            return values
        types = {e["key"]: e["type"] for e in self._extset["settings"]}
        for widget_id, key in self._extset_widgets.items():
            if types.get(key) == "button":
                continue
            widget = self.query_one(f"#{widget_id}")
            if isinstance(widget, Switch):
                values[key] = bool(widget.value)
            elif isinstance(widget, Select):
                values[key] = str(widget.value)
            elif isinstance(widget, Input):
                values[key] = widget.value
        return values

    async def _save_extension(self) -> None:
        if not self._extset:
            self._extset_say("Choose an extension first.")
            return
        name = self._extset["name"]
        result = await asyncio.to_thread(
            self._settings_api().save_extension_settings,
            name,
            self._collect_extension_values(),
        )
        if result.get("error"):
            self._extset_say(result["error"])
            return
        await self._show_extension(name)
        self._extset_say(f"Saved settings for {result.get('display_name') or name}.")

    async def _reset_extension(self) -> None:
        if not self._extset:
            self._extset_say("Choose an extension first.")
            return
        name = self._extset["name"]
        result = await asyncio.to_thread(
            self._settings_api().reset_extension_settings, name
        )
        if result.get("error"):
            self._extset_say(result["error"])
            return
        await self._show_extension(name)
        self._extset_say(f"Settings of {name} reset to their defaults.")

    async def _run_extension_action(self, key: str) -> None:
        if not self._extset:
            return
        result = await asyncio.to_thread(
            self._settings_api().run_extension_setting_action,
            self._extset["name"],
            key,
        )
        self._extset_say(result.get("error") or "Done.")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "extset-picker":
            return
        if isinstance(event.value, str) and event.value:
            self.run_worker(self._show_extension(event.value), exclusive=True)

    # ------------------------------------------------------------------

    def _say(self, message: str) -> None:
        self.query_one("#registry-status", Label).update(message)

    async def reload(self) -> None:
        try:
            from ..extensions import registry_config

            registries = await asyncio.to_thread(registry_config.list_registries)
        except Exception as exc:
            self._say(f"Could not read the registry list — {exc}")
            return

        table = self.query_one("#registry-table", DataTable)
        table.clear()
        self._urls = []
        for entry in registries:
            sources = ", ".join(
                SOURCE_LABELS.get(source, source) for source in entry.get("sources", [])
            )
            table.add_row(
                str(entry.get("url", "")),
                sources,
                "enabled" if entry.get("enabled") else "removed",
            )
            self._urls.append(str(entry.get("url", "")))

        if not self._urls:
            self._say("No registry links configured.")
        else:
            self._say(f"{len(self._urls)} link(s).")

    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "extset-save":
            self.run_worker(self._save_extension(), exclusive=True)
            return
        if button_id == "extset-reset":
            self.run_worker(self._reset_extension(), exclusive=True)
            return
        if button_id in getattr(self, "_extset_widgets", {}):
            self.run_worker(
                self._run_extension_action(self._extset_widgets[button_id]),
                exclusive=True,
            )
            return
        if self._busy:
            return
        if event.button.id == "registry-add":
            url = self.query_one("#registry-url", Input).value.strip()
            if not url:
                self._say("Paste a registry link first.")
                return
            self.run_worker(self._add(url), exclusive=True)
        elif event.button.id == "registry-remove":
            url = self._selected_url()
            if not url:
                self._say("Select a link in the table first.")
                return
            self.run_worker(self._remove(url), exclusive=True)

    def _selected_url(self) -> str:
        table = self.query_one("#registry-table", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._urls)):
            return ""
        return self._urls[row]

    async def _add(self, url: str) -> None:
        self._busy = True
        self._say(f"Adding {url}…")
        # `finally`, not a reset per exit: clearing the flag by hand covered
        # the two paths anyone thinks of and neither of the ones that
        # actually break — a raise out of query_one(), reload() or _sync()
        # left the view busy for the rest of the session, with the Add and
        # Remove buttons dead and no way back but a restart.
        try:
            try:
                from ..extensions import registry_config

                await asyncio.to_thread(registry_config.add_registry, url)
            except Exception as exc:
                self._say(f"Could not add it — {exc}")
                return

            self.query_one("#registry-url", Input).value = ""
            await self.reload()
            await self._sync()
        finally:
            self._busy = False

    async def _remove(self, url: str) -> None:
        self._busy = True
        self._say(f"Removing {url}…")
        try:
            try:
                from ..extensions import registry_config

                await asyncio.to_thread(registry_config.remove_registry, url)
            except Exception as exc:
                self._say(f"Could not remove it — {exc}")
                return

            await self.reload()
        finally:
            # Same as _add: a reload() that raises must not cost the view
            # every later add and remove.
            self._busy = False

    async def _sync(self) -> None:
        """Installs from the new list now, not at the next launch."""
        self._say("Installing providers from the registries…")
        try:
            from ..extensions.manager import ExtensionManager

            await asyncio.to_thread(
                ExtensionManager,
                auto_install_downloads=True,
                min_trust_tier=self._min_trust_tier,
            )
        except Exception as exc:
            self._say(f"Added, but installing from it failed — {exc}")
            return
        self._say("Added. Providers installed from the registries.")
