#!/usr/bin/env python3
"""Drillbit TUI — AI-powered Fedora package discovery."""

from __future__ import annotations

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Label,
    LoadingIndicator,
    SelectionList,
    Static,
)
from textual.widgets.selection_list import Selection

BACKEND_URL = "http://localhost:8000"

# (package dict key, display label, visible by default)
AVAILABLE_COLUMNS: list[tuple[str, str, bool]] = [
    ("name",         "Package",      True),
    ("summary",      "Summary",      True),
    ("score",        "Score",        False),
    ("reason",       "Reason",       False),
    ("copr_project", "COPR Project", False),
    ("license",      "License",      False),
    ("last_updated", "Last Updated", False),
]

ASCII_ART = r"""
        /\          /\          /\
       /  \   /\   /  \   /\   /  \
      / /\ \ /  \ / /\ \ /  \ / /\ \
     /_/  \_/    \_/  \_/    \_/  \_\
    |       F E D O R A   R O C K    |
    |_________________________________|
              | | | | |
            .-----------.
            |   D R I L L
            |     B I T  |
            '-----------'
                 \|/
                  V
"""

APP_TITLE = r"""
  ___  ____  __  __  __    ____  __  ____
 |   \|  _ \|  ||  ||  |  |  _ \|  ||_  _|
 | o  ) /\ \|  ||  ||  |_ | __ /|  |  ||
 |___/|_||_/|__||__||____||_|   |__|  |_|
"""

DRILLBIT_CSS = """
Screen {
    background: transparent;
}

Screen > * {
    background: transparent;
}

#header-container {
    height: auto;
    align: center middle;
    padding: 1 0 0 0;
    background: transparent;
}

#ascii-art {
    text-align: center;
    height: auto;
    background: transparent;
}

#app-title {
    text-align: center;
    height: auto;
    background: transparent;
}

#tagline {
    text-align: center;
    height: auto;
    padding: 0 0 1 0;
    background: transparent;
}

#search-container {
    height: auto;
    align: center middle;
    padding: 1 4;
    background: transparent;
}

#search-label {
    text-align: center;
    height: auto;
    padding: 0 0 1 0;
    background: transparent;
}

#search-input {
    width: 70%;
    padding: 0 2;
    background: transparent;
}

#status-bar {
    height: auto;
    text-align: center;
    padding: 0 4;
    background: transparent;
}

#status-bar.error {
    color: red;
}

#status-bar.success {
    color: green;
}

#loading {
    height: 3;
    display: none;
    background: transparent;
}

#loading.visible {
    display: block;
}

#results-area {
    height: 1fr;
    background: transparent;
}

#results-container {
    height: 1fr;
    padding: 1 4 1 4;
    background: transparent;
}

#results-title {
    height: auto;
    padding: 0 0 1 0;
    background: transparent;
}

#results-table {
    height: 1fr;
    background: transparent;
}

#column-picker {
    width: 26;
    display: none;
    background: transparent;
    border-left: solid $panel;
    padding: 1 1;
}

#column-picker.visible {
    display: block;
}

#column-picker-title {
    height: auto;
    padding: 0 0 1 0;
    text-align: center;
    background: transparent;
}

SelectionList {
    background: transparent;
    border: none;
    height: auto;
}

Footer {
    background: transparent;
}
"""


class DrillbitApp(App):
    """Drillbit — AI-powered Fedora package discovery TUI."""

    CSS = DRILLBIT_CSS

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_results", "Clear", show=True),
        Binding("c", "toggle_columns", "Columns", show=True),
        Binding("escape", "escape_pressed", "Back", show=False),
        Binding("f1", "focus_search", "Search", show=True),
    ]

    is_loading: reactive[bool] = reactive(False)
    status_message: reactive[str] = reactive("")
    status_type: reactive[str] = reactive("info")
    columns_open: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self._last_results: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="header-container"):
            yield Static(ASCII_ART, id="ascii-art")
            yield Static(APP_TITLE, id="app-title")
            yield Static("AI-powered package discovery for Fedora", id="tagline")

        with Vertical(id="search-container"):
            yield Label("What do you need? Describe it in plain English:", id="search-label")
            with Center():
                yield Input(
                    placeholder='e.g. "a tool for editing video files" or "screen recorder"',
                    id="search-input",
                )
            yield Static("", id="status-bar")
            yield LoadingIndicator(id="loading")

        with Horizontal(id="results-area"):
            with Vertical(id="results-container"):
                yield Static("[ Results ]  (c: toggle columns)", id="results-title")
                yield DataTable(id="results-table", zebra_stripes=False, cursor_type="row")

            with Vertical(id="column-picker"):
                yield Static("Columns", id="column-picker-title")
                yield SelectionList(
                    *[
                        Selection(label, key, initial)
                        for key, label, initial in AVAILABLE_COLUMNS
                    ],
                    id="column-list",
                )

        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_columns()
        self.query_one("#search-input", Input).focus()

    # ── column picker ──────────────────────────────────────────────────────

    def _visible_columns(self) -> list[tuple[str, str]]:
        """Return (key, label) for every currently selected column."""
        selected: set[str] = set(self.query_one("#column-list", SelectionList).selected)
        return [(key, label) for key, label, _ in AVAILABLE_COLUMNS if key in selected]

    def _rebuild_columns(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        for _, label in self._visible_columns():
            table.add_column(label, key=label)
        if self._last_results:
            self._fill_rows()

    def _fill_rows(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        visible = self._visible_columns()
        for i, pkg in enumerate(self._last_results, 1):
            row: list = []
            for key, _ in visible:
                row.append(self._render_cell(key, pkg, i))
            table.add_row(*row)

    def _render_cell(self, key: str, pkg: dict, rank: int) -> str | Text:
        value = pkg.get(key, "—") or "—"
        if key == "name":
            return Text(str(value), style="bold" if rank == 1 else "")
        if key == "score":
            try:
                s = float(value)
                pct = f"{s * 100:.0f}%"
                if s >= 0.7:
                    return Text(pct, style="bold green")
                if s >= 0.4:
                    return Text(pct, style="bold yellow")
                return Text(pct, style="bold red")
            except (TypeError, ValueError):
                return str(value)
        if key == "summary":
            return str(value)[:72] + "…" if len(str(value)) > 72 else str(value)
        if key == "reason":
            return str(value)[:80] + "…" if len(str(value)) > 80 else str(value)
        return str(value)

    @on(SelectionList.SelectedChanged, "#column-list")
    def on_column_selection_changed(self) -> None:
        self._rebuild_columns()

    def watch_columns_open(self, value: bool) -> None:
        picker = self.query_one("#column-picker")
        if value:
            picker.add_class("visible")
        else:
            picker.remove_class("visible")

    def action_toggle_columns(self) -> None:
        self.columns_open = not self.columns_open
        if self.columns_open:
            self.query_one("#column-list", SelectionList).focus()
        else:
            self.query_one("#search-input", Input).focus()

    # ── loading / status ───────────────────────────────────────────────────

    def watch_is_loading(self, value: bool) -> None:
        loading = self.query_one("#loading", LoadingIndicator)
        if value:
            loading.add_class("visible")
        else:
            loading.remove_class("visible")

    def watch_status_message(self, value: str) -> None:
        self.query_one("#status-bar", Static).update(value)

    def watch_status_type(self, value: str) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.remove_class("error", "success")
        if value in ("error", "success"):
            bar.add_class(value)

    # ── search ─────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.run_search(query)

    @work(exclusive=True, thread=True)
    def run_search(self, query: str) -> None:
        self.call_from_thread(self._set_loading, True)
        self.call_from_thread(self._set_status, f'Drilling into packages for: "{query}"...', "info")

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(f"{BACKEND_URL}/search", params={"q": query, "limit": 7})
                resp.raise_for_status()
                packages = resp.json()
        except httpx.ConnectError:
            self.call_from_thread(
                self._set_status,
                "Cannot reach backend at localhost:8000 — is the stack running? (podman-compose up -d)",
                "error",
            )
            self.call_from_thread(self._set_loading, False)
            return
        except httpx.HTTPStatusError as e:
            self.call_from_thread(
                self._set_status,
                f"Backend error: {e.response.status_code}",
                "error",
            )
            self.call_from_thread(self._set_loading, False)
            return
        except Exception as e:
            self.call_from_thread(self._set_status, f"Error: {e}", "error")
            self.call_from_thread(self._set_loading, False)
            return

        self.call_from_thread(self._update_results, packages, query)
        self.call_from_thread(self._set_loading, False)

    def _set_loading(self, value: bool) -> None:
        self.is_loading = value

    def _set_status(self, msg: str, kind: str = "info") -> None:
        self.status_message = msg
        self.status_type = kind

    def _update_results(self, packages: list[dict], query: str) -> None:
        if not packages:
            self._set_status(f'No packages found for "{query}". Try a different description.', "error")
            return
        self._last_results = packages
        self._fill_rows()
        count = len(packages)
        self._set_status(
            f"Found {count} package{'s' if count != 1 else ''} — arrow keys to browse, c to pick columns",
            "success",
        )

    # ── misc actions ───────────────────────────────────────────────────────

    def action_clear_results(self) -> None:
        self._last_results = []
        table = self.query_one("#results-table", DataTable)
        table.clear()
        search = self.query_one("#search-input", Input)
        search.clear()
        self._set_status("", "info")
        self.columns_open = False
        search.focus()

    def action_escape_pressed(self) -> None:
        if self.columns_open:
            self.columns_open = False
            self.query_one("#search-input", Input).focus()
        else:
            self.query_one("#search-input", Input).blur()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()


if __name__ == "__main__":
    DrillbitApp().run()
