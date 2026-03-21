#!/usr/bin/env bash
# Installs the Drillbit GNOME Shell Search Provider for the current user.
# Run from the gnome-search-provider/ directory.
# Requires: python3-dbus python3-gobject (system packages) + httpx (pip)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Directories ───────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/share/drillbit"
DESKTOP_DIR="$HOME/.local/share/applications"
SEARCH_PROVIDER_DIR="$HOME/.local/share/gnome-shell/search-providers"
DBUS_SERVICES_DIR="$HOME/.local/share/dbus-1/services"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

mkdir -p "$INSTALL_DIR" "$DESKTOP_DIR" "$SEARCH_PROVIDER_DIR" \
         "$DBUS_SERVICES_DIR" "$SYSTEMD_USER_DIR"

# ── Copy search provider script ───────────────────────────────────────────────
cp "$SCRIPT_DIR/search_provider.py" "$INSTALL_DIR/search_provider.py"
chmod +x "$INSTALL_DIR/search_provider.py"

# ── Desktop entry ─────────────────────────────────────────────────────────────
sed "s|INSTALL_PATH|$INSTALL_DIR|g" \
    "$SCRIPT_DIR/drillbit.desktop" \
    > "$DESKTOP_DIR/drillbit.desktop"
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

# ── GNOME Shell search provider registration ──────────────────────────────────
cp "$SCRIPT_DIR/drillbit.search-provider.ini" \
   "$SEARCH_PROVIDER_DIR/drillbit.search-provider.ini"

# ── D-Bus session service (on-demand activation) ─────────────────────────────
sed "s|INSTALL_PATH|$INSTALL_DIR|g" \
    "$SCRIPT_DIR/org.drillbit.SearchProvider.service" \
    > "$DBUS_SERVICES_DIR/org.drillbit.SearchProvider.service"

# ── Systemd user service ──────────────────────────────────────────────────────
sed "s|INSTALL_PATH|$INSTALL_DIR|g" \
    "$SCRIPT_DIR/drillbit-search-provider.service" \
    > "$SYSTEMD_USER_DIR/drillbit-search-provider.service"

systemctl --user daemon-reload
systemctl --user enable --now drillbit-search-provider.service

echo ""
echo "================================================================"
echo "  Drillbit Search Provider — installed successfully"
echo "================================================================"
echo ""
echo "STEP 1 — Start the backend (if not already running):"
echo ""
echo "    cd $(dirname "$SCRIPT_DIR") && podman-compose up -d"
echo ""
echo "STEP 2 — Verify the service is running:"
echo ""
echo "    systemctl --user status drillbit-search-provider"
echo ""
echo "    You should see 'active (running)'. If it shows failed, check:"
echo "    journalctl --user -u drillbit-search-provider -n 50"
echo ""
echo "STEP 3 — Log out and log back in."
echo ""
echo "    GNOME Shell only picks up new search providers at login."
echo "    There is no command to hot-reload it — a full log out/in is required."
echo ""
echo "STEP 4 — Enable Drillbit in GNOME Settings (required on GNOME 40+):"
echo ""
echo "    Settings → Search → scroll down → toggle 'Drillbit' ON"
echo ""
echo "    If Drillbit does not appear in the list, the desktop entry or"
echo "    search provider file was not picked up. Run:"
echo "    desktop-file-validate $DESKTOP_DIR/drillbit.desktop"
echo ""
echo "STEP 5 — Test it:"
echo ""
echo "    Open the Activities overlay (Super key) and type:"
echo "      video editor"
echo "      pdf viewer"
echo "      screen recorder"
echo ""
echo "    Drillbit results should appear within ~2 seconds."
echo ""
echo "----------------------------------------------------------------"
echo "Useful commands:"
echo "  Logs:   journalctl --user -u drillbit-search-provider -f"
echo "  Status: systemctl --user status drillbit-search-provider"
echo "  Stop:   systemctl --user stop drillbit-search-provider"
echo "================================================================"
