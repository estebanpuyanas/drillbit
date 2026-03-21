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

# ── gsettings: ensure provider is not disabled and appears in sort-order ──────
DESKTOP_ID="drillbit.desktop"

# Remove from disabled list if present
DISABLED=$(gsettings get org.gnome.desktop.search-providers disabled 2>/dev/null || echo "@as []")
if echo "$DISABLED" | grep -q "$DESKTOP_ID"; then
    NEW_DISABLED=$(echo "$DISABLED" | sed "s|'$DESKTOP_ID', ||g; s|, '$DESKTOP_ID'||g; s|'$DESKTOP_ID'||g")
    gsettings set org.gnome.desktop.search-providers disabled "$NEW_DISABLED"
fi

# Prepend to sort-order list if not already present
SORT=$(gsettings get org.gnome.desktop.search-providers sort-order 2>/dev/null || echo "@as []")
if ! echo "$SORT" | grep -q "$DESKTOP_ID"; then
    if [ "$SORT" = "@as []" ] || [ "$SORT" = "[]" ]; then
        gsettings set org.gnome.desktop.search-providers sort-order "['$DESKTOP_ID']"
    else
        NEW_SORT=$(echo "$SORT" | sed "s/\[/['$DESKTOP_ID', /")
        gsettings set org.gnome.desktop.search-providers sort-order "$NEW_SORT"
    fi
fi

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
echo "STEP 4 — Confirm Drillbit is enabled in GNOME Settings:"
echo ""
echo "    Settings → Search → scroll down → confirm 'Drillbit' is toggled ON"
echo ""
echo "    The installer enabled it automatically via gsettings. If it still"
echo "    does not appear in the list, the desktop entry was not picked up. Run:"
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
