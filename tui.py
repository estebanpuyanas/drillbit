#!/usr/bin/env python3
"""Drillbit TUI — AI-powered Fedora package discovery."""

from __future__ import annotations

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, ScrollableContainer, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Label,
    LoadingIndicator,
    Static,
)

BACKEND_URL = "http://localhost:8000"

ASCII_ART = r"""
         /\            /\         /\
        /  \     /\   /  \   /\  /  \
       / /\ \   /  \ / /\ \ /  \/    \
      / /  \ \ / /\ / /  \/    /  /\  \
     /______\/ /  // /____\___/__/  \  \
    |          \  \\ |   FEDORA      \  |
    |    R O C K \ \\|   MOUNTAIN    /  |
    |_____________\_\\______________/   |
                   |||||||
                 .---------.
                |   DRILL   |
                |    BIT    |
                 '---------'
                    \   /
                     \ /
                      V
"""

APP_TITLE = r"""
  ___  ____  __  __    __    ____  ____  ____
 / __)( __ \(  )(  )  (  )  (  _ \(  _ \(_  _)
( (__  )   / )( / (_/\ )(    ) _ < )  _/  )(
 \___)(_)\_)(__)\_____/(__)  (____/(_)   (__)
"""


DRILLBIT_CSS = """
Screen {
    background: #1a1a2e;
    color: #e0e0e0;
}

#header-container {
    height: auto;
    align: center middle;
    padding: 1 0 0 0;
}

#ascii-art {
    color: #f5a623;
    text-align: center;
    height: auto;
}

#app-title {
    color: #e8c547;
    text-align: center;
    height: auto;
}

#tagline {
    color: #888888;
    text-align: center;
    height: auto;
    padding: 0 0 1 0;
}

#search-container {
    height: auto;
    align: center middle;
    padding: 1 4;
}

#search-label {
    color: #f5a623;
    text-align: center;
    height: auto;
    padding: 0 0 1 0;
}

#search-input {
    width: 70%;
    border: tall #f5a623;
    background: #16213e;
    color: #e0e0e0;
    padding: 0 2;
}

#search-input:focus {
    border: tall #e8c547;
}

#status-bar {
    height: auto;
    text-align: center;
    color: #888888;
    padding: 0 4;
}

#status-bar.error {
    color: #e74c3c;
}

#status-bar.success {
    color: #2ecc71;
}

#loading {
    height: 3;
    display: none;
    color: #f5a623;
}

#loading.visible {
    display: block;
}

#results-container {
    height: 1fr;
    padding: 1 4;
    border: tall #2d2d4e;
}

#results-title {
    color: #f5a623;
    text-align: center;
    height: auto;
    padding: 0 0 1 0;
}

#results-table {
    height: 1fr;
    background: #16213e;
}

DataTable > .datatable--header {
    background: #2d2d4e;
    color: #f5a623;
}

DataTable > .datatable--cursor {
    background: #3d3d6e;
}

DataTable > .datatable--fixed {
    background: #2d2d4e;
    color: #f5a623;
}

Footer {
    background: #16213e;
    color: #888888;
}
"""


class DrillbitApp(App):
    """Drillbit — AI-powered Fedora package discovery TUI."""

    CSS = DRILLBIT_CSS

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_results", "Clear", show=True),
        Binding("escape", "blur_search", "Blur search", show=False),
        Binding("f1", "focus_search", "Search", show=True),
    ]

    is_loading: reactive[bool] = reactive(False)
    status_message: reactive[str] = reactive("")
    status_type: reactive[str] = reactive("info")

    def compose(self) -> ComposeResult:
        # Header section with ASCII art and title
        with Vertical(id="header-container"):
            yield Static(ASCII_ART, id="ascii-art")
            yield Static(APP_TITLE, id="app-title")
            yield Static(
                "AI-powered package discovery for Fedora — describe what you need in plain English",
                id="tagline",
            )

        # Search section
        with Vertical(id="search-container"):
            yield Label("What do you need? Describe it in plain English:", id="search-label")
            with Center():
                yield Input(
                    placeholder='e.g. "a tool for editing video files" or "screen recorder"',
                    id="search-input",
                )
            yield Static("", id="status-bar")
            yield LoadingIndicator(id="loading")

        # Results section
        with Vertical(id="results-container"):
            yield Static("[ Results ]", id="results-title")
            yield DataTable(id="results-table", zebra_stripes=True, cursor_type="row")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns(
            Text("#", style="bold"),
            Text("Package", style="bold"),
            Text("Score", style="bold"),
            Text("Summary", style="bold"),
            Text("Reason", style="bold"),
        )
        self.query_one("#search-input", Input).focus()

    def watch_is_loading(self, value: bool) -> None:
        loading = self.query_one("#loading", LoadingIndicator)
        if value:
            loading.add_class("visible")
        else:
            loading.remove_class("visible")

    def watch_status_message(self, value: str) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(value)

    def watch_status_type(self, value: str) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.remove_class("error", "success")
        if value in ("error", "success"):
            bar.add_class(value)

    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
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
        table = self.query_one("#results-table", DataTable)
        table.clear()

        if not packages:
            self._set_status(f'No packages found for "{query}". Try a different description.', "error")
            return

        for i, pkg in enumerate(packages, 1):
            name = pkg.get("name", "—")
            score = pkg.get("score", 0.0)
            summary = pkg.get("summary", "—")
            reason = pkg.get("reason", "—")

            score_pct = f"{score * 100:.0f}%" if isinstance(score, float) else str(score)

            # Color the score
            if isinstance(score, float) and score >= 0.7:
                score_text = Text(score_pct, style="bold green")
            elif isinstance(score, float) and score >= 0.4:
                score_text = Text(score_pct, style="bold yellow")
            else:
                score_text = Text(score_pct, style="bold red")

            rank_text = Text(str(i), style="bold #f5a623" if i == 1 else "")
            name_text = Text(name, style="bold cyan" if i == 1 else "cyan")

            # Truncate long text for display
            summary_display = summary[:60] + "…" if len(summary) > 60 else summary
            reason_display = reason[:70] + "…" if len(reason) > 70 else reason

            table.add_row(rank_text, name_text, score_text, summary_display, reason_display)

        count = len(packages)
        self._set_status(f"Found {count} package{'s' if count != 1 else ''} — use arrow keys to browse", "success")

    def action_clear_results(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        search = self.query_one("#search-input", Input)
        search.clear()
        self._set_status("", "info")
        search.focus()

    def action_blur_search(self) -> None:
        self.query_one("#search-input", Input).blur()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()


if __name__ == "__main__":
    DrillbitApp().run()
