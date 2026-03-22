#!/usr/bin/python3
"""
Drillbit GNOME Shell Search Provider

Implements the org.gnome.Shell.SearchProvider2 D-Bus interface.
GNOME Shell calls this service when the user types in the Activities overlay,
and displays the results inline as clickable package entries.

Query protocol:
  Wrap your search in double quotes: "video editor"
  The provider ignores partial queries (no closing quote) and only fires
  the backend 800 ms after the closing quote is typed, so every keystroke
  does not trigger an LLM call.

System deps (Fedora):
    sudo dnf install python3-dbus python3-gobject
Pip deps:
    pip install httpx
"""

import logging
import subprocess
import threading

import dbus
import dbus.mainloop.glib
import dbus.service
import httpx
from gi.repository import GLib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [drillbit] %(levelname)s %(message)s",
)
log = logging.getLogger("drillbit-search-provider")

IFACE = "org.gnome.Shell.SearchProvider2"
BUS_NAME = "org.drillbit.SearchProvider"
OBJECT_PATH = "/org/drillbit/SearchProvider"
BACKEND_URL = "http://localhost:8000"

# How long to wait after a complete quoted phrase before firing the backend.
DEBOUNCE_MS = 800

# Minimum query length (after stripping quotes) to bother querying.
MIN_QUERY_LEN = 3


class DrillbitSearchProvider(dbus.service.Object):
    def __init__(self, conn):
        super().__init__(conn, OBJECT_PATH)
        # Map package name → package dict, populated on each search.
        self._cache: dict[str, dict] = {}
        # Debounce state — all touched only from the GLib main thread.
        self._pending_timer_id: int | None = None
        self._pending_query: str | None = None
        self._pending_return_cb = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_quoted_query(self, terms) -> str | None:
        """
        Return the query string if terms form a complete quoted phrase
        (first token starts with '"', last token ends with '"'), else None.

        Example: ['"video', 'editor"'] → 'video editor'
        """
        joined = " ".join(str(t) for t in terms)
        if joined.startswith('"') and joined.endswith('"') and len(joined) >= MIN_QUERY_LEN + 2:
            return joined[1:-1].strip()
        return None

    def _cancel_pending(self):
        """Cancel any in-flight debounce timer and resolve its callback with []."""
        if self._pending_timer_id is not None:
            GLib.source_remove(self._pending_timer_id)
            self._pending_timer_id = None
        if self._pending_return_cb is not None:
            try:
                self._pending_return_cb([])
            except Exception:
                pass
            self._pending_return_cb = None
        self._pending_query = None

    def _schedule_search(self, query: str, return_cb):
        """Cancel any pending search and schedule a new one after DEBOUNCE_MS."""
        self._cancel_pending()
        self._pending_query = query
        self._pending_return_cb = return_cb
        self._pending_timer_id = GLib.timeout_add(DEBOUNCE_MS, self._on_debounce_fire)
        log.info("Debounce armed for %r (%d ms)", query, DEBOUNCE_MS)

    def _on_debounce_fire(self) -> bool:
        """GLib timer callback — clear state and dispatch to worker thread."""
        self._pending_timer_id = None
        query = self._pending_query
        return_cb = self._pending_return_cb
        self._pending_query = None
        self._pending_return_cb = None
        log.info("Debounce fired, querying backend for %r", query)
        threading.Thread(
            target=self._search_thread,
            args=(query, return_cb),
            daemon=True,
        ).start()
        return GLib.SOURCE_REMOVE

    def _search_thread(self, query: str, return_cb):
        """Worker thread: call backend, then hand result back to main loop."""
        ids = self._query_backend(query)
        GLib.idle_add(return_cb, ids)

    def _query_backend(self, query: str) -> list[str]:
        """Call the FastAPI backend and return a list of result IDs."""
        try:
            resp = httpx.get(
                f"{BACKEND_URL}/search",
                params={"q": query},
                timeout=30.0,
            )
            resp.raise_for_status()
            packages = resp.json()
            ids: list[str] = []
            for pkg in packages:
                pkg_id = pkg["name"]
                self._cache[pkg_id] = pkg
                ids.append(pkg_id)
            log.info("Search %r → %d results", query, len(ids))
            return ids
        except Exception as exc:
            log.warning("Backend query failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # SearchProvider2 interface
    # ------------------------------------------------------------------

    @dbus.service.method(
        IFACE,
        in_signature="as",
        out_signature="as",
        async_callbacks=("_return_cb", "_error_cb"),
    )
    def GetInitialResultSet(self, terms, _return_cb, _error_cb):
        query = self._extract_quoted_query(terms)
        if query is None:
            self._cancel_pending()
            _return_cb([])
            return
        self._schedule_search(query, _return_cb)

    @dbus.service.method(
        IFACE,
        in_signature="asas",
        out_signature="as",
        async_callbacks=("_return_cb", "_error_cb"),
    )
    def GetSubsearchResultSet(self, previous_results, terms, _return_cb, _error_cb):
        query = self._extract_quoted_query(terms)
        if query is None:
            self._cancel_pending()
            _return_cb([])
            return
        self._schedule_search(query, _return_cb)

    @dbus.service.method(IFACE, in_signature="as", out_signature="aa{sv}")
    def GetResultMetas(self, identifiers):
        metas = []
        for pkg_id in identifiers:
            pkg_id = str(pkg_id)
            pkg = self._cache.get(pkg_id, {})
            metas.append(
                {
                    "id": dbus.String(pkg_id),
                    "name": dbus.String(pkg.get("name", pkg_id)),
                    "description": dbus.String(pkg.get("summary", "Fedora package")),
                    "clipboardText": dbus.String(f"sudo dnf install {pkg_id}"),
                    "gicon": dbus.String("package-x-generic"),
                }
            )
        return metas

    @dbus.service.method(IFACE, in_signature="sasu", out_signature="")
    def ActivateResult(self, identifier, terms, timestamp):
        """User clicked a result — open a terminal and run dnf install."""
        pkg_id = str(identifier)
        name = self._cache.get(pkg_id, {}).get("name", pkg_id)
        log.info("ActivateResult: %s", name)
        try:
            subprocess.Popen(
                [
                    "gnome-terminal",
                    "--",
                    "bash",
                    "-c",
                    f"pkexec dnf install -y {name}; echo; read -p 'Done — press Enter to close.'",
                ]
            )
        except FileNotFoundError:
            try:
                subprocess.Popen(
                    [
                        "xterm",
                        "-e",
                        f"pkexec dnf install -y {name}; read -p 'Done — press Enter to close.'",
                    ]
                )
            except Exception as exc:
                log.error("Could not launch terminal: %s", exc)

    @dbus.service.method(IFACE, in_signature="asu", out_signature="")
    def LaunchSearch(self, terms, timestamp):
        log.info("LaunchSearch: %r", list(terms))


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    session_bus = dbus.SessionBus()
    _name = dbus.service.BusName(BUS_NAME, session_bus)
    _provider = DrillbitSearchProvider(session_bus)
    log.info("Drillbit search provider running on %s", BUS_NAME)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
