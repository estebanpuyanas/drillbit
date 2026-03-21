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
cp "$SCRIPT_DIR/drillbit.desktop" "$DESKTOP_DIR/drillbit.desktop"

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
echo "✓ Drillbit search provider installed to $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. Make sure the backend is running:  podman-compose up -d"
echo "  2. Log out and back in (or run: gnome-shell --replace &)"
echo "  3. Open Activities and type a query like 'video editor' or 'pdf viewer'"
echo ""
echo "Logs:  journalctl --user -u drillbit-search-provider -f"
echo "Status: systemctl --user status drillbit-search-provider"
