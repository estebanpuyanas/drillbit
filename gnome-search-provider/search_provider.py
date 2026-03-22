#!/usr/bin/python3
"""
Drillbit GNOME Shell Search Provider

Implements the org.gnome.Shell.SearchProvider2 D-Bus interface.
GNOME Shell calls this service when the user types in the Activities overlay,
and displays the results inline as clickable package entries.

System deps (Fedora):
    sudo dnf install python3-dbus python3-gobject
Pip deps:
    pip install httpx
"""

import logging
import subprocess

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


class DrillbitSearchProvider(dbus.service.Object):
    def __init__(self, conn):
        super().__init__(conn, OBJECT_PATH)
        # Map package name → package dict, populated on each search
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_backend(self, query: str) -> list[str]:
        """Call the FastAPI backend and return a list of result IDs."""
        try:
            resp = httpx.get(
                f"{BACKEND_URL}/search",
                params={"q": query},
                timeout=8.0,
            )
            resp.raise_for_status()
            packages = resp.json()
            ids: list[str] = []
            for pkg in packages:
                pkg_id = pkg["name"]
                self._cache[pkg_id] = pkg
                ids.append(pkg_id)
            log.info("Search %r → %s results", query, len(ids))
            return ids
        except Exception as exc:
            log.warning("Backend query failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # SearchProvider2 interface
    # ------------------------------------------------------------------

    @dbus.service.method(IFACE, in_signature="as", out_signature="as")
    def GetInitialResultSet(self, terms):
        return self._query_backend(" ".join(str(t) for t in terms))

    @dbus.service.method(IFACE, in_signature="asas", out_signature="as")
    def GetSubsearchResultSet(self, previous_results, terms):
        return self._query_backend(" ".join(str(t) for t in terms))

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
                    # Shown in the clipboard tooltip and used by some shell themes
                    "clipboardText": dbus.String(f"sudo dnf install {pkg_id}"),
                    # Generic package icon — works without any icon theme extras
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
            # Fallback for non-GNOME terminals
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
        """User pressed Enter on the search row — nothing to launch for now."""
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
