#!/usr/bin/env bash

# Installs the Drillbit GNOME Shell Search Provider system-wide.
# Run with sudo: sudo bash install.sh

set -euo pipefail

# /usr/local/ paths require root; preserve the calling user for user-scoped steps
if [[ $EUID -ne 0 ]]; then
    echo "Re-running with sudo..."
    exec sudo --preserve-env=HOME,USER,DBUS_SESSION_BUS_ADDRESS,XDG_RUNTIME_DIR \
        bash "$0" "$@"
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")
REAL_UID=$(id -u "$REAL_USER")
# Session bus vars needed for systemctl --user and gsettings when running as root
USER_BUS="unix:path=/run/user/${REAL_UID}/bus"
USER_RUNTIME="/run/user/${REAL_UID}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Paths (matching /usr/local layout from reference implementation) ───────────
SCRIPT_DEST="/usr/local/bin/drillbit-search-provider"
DESKTOP_DIR="/usr/local/share/applications"
SEARCH_PROVIDER_DIR="/usr/local/share/gnome-shell/search-providers"
DBUS_SERVICES_DIR="/usr/local/share/dbus-1/services"
SYSTEMD_USER_DIR="$REAL_HOME/.config/systemd/user"

mkdir -p "$DESKTOP_DIR" "$SEARCH_PROVIDER_DIR" \
         "$DBUS_SERVICES_DIR" "$SYSTEMD_USER_DIR"

# ── Install search provider script ────────────────────────────────────────────
cp "$SCRIPT_DIR/search_provider.py" "$SCRIPT_DEST"
chmod 0755 "$SCRIPT_DEST"

# ── Desktop entry ─────────────────────────────────────────────────────────────
sed "s|SCRIPT_PATH|$SCRIPT_DEST|g" \
    "$SCRIPT_DIR/drillbit.desktop" \
    > "$DESKTOP_DIR/drillbit.desktop"
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

# ── GNOME Shell search provider registration ──────────────────────────────────
cp "$SCRIPT_DIR/drillbit.search-provider.ini" \
   "$SEARCH_PROVIDER_DIR/drillbit.search-provider.ini"

# ── D-Bus session service (on-demand activation) ──────────────────────────────
sed "s|SCRIPT_PATH|$SCRIPT_DEST|g" \
    "$SCRIPT_DIR/org.drillbit.SearchProvider.service" \
    > "$DBUS_SERVICES_DIR/org.drillbit.SearchProvider.service"

# ── Systemd user service ──────────────────────────────────────────────────────
sed "s|SCRIPT_PATH|$SCRIPT_DEST|g" \
    "$SCRIPT_DIR/drillbit-search-provider.service" \
    > "$SYSTEMD_USER_DIR/drillbit-search-provider.service"

sudo -u "$REAL_USER" \
    DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
    XDG_RUNTIME_DIR="$USER_RUNTIME" \
    systemctl --user daemon-reload

sudo -u "$REAL_USER" \
    DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
    XDG_RUNTIME_DIR="$USER_RUNTIME" \
    systemctl --user enable --now drillbit-search-provider.service

# ── gsettings: ensure provider is not disabled and appears in sort-order ──────
DESKTOP_ID="drillbit.desktop"

# Remove from disabled list if present
DISABLED=$(sudo -u "$REAL_USER" \
    DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
    XDG_RUNTIME_DIR="$USER_RUNTIME" \
    gsettings get org.gnome.desktop.search-providers disabled 2>/dev/null || echo "@as []")
if echo "$DISABLED" | grep -q "$DESKTOP_ID"; then
    NEW_DISABLED=$(echo "$DISABLED" | sed "s|'$DESKTOP_ID', ||g; s|, '$DESKTOP_ID'||g; s|'$DESKTOP_ID'||g")
    sudo -u "$REAL_USER" \
        DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
        XDG_RUNTIME_DIR="$USER_RUNTIME" \
        gsettings set org.gnome.desktop.search-providers disabled "$NEW_DISABLED"
fi

# Prepend to sort-order list if not already present
SORT=$(sudo -u "$REAL_USER" \
    DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
    XDG_RUNTIME_DIR="$USER_RUNTIME" \
    gsettings get org.gnome.desktop.search-providers sort-order 2>/dev/null || echo "@as []")
if ! echo "$SORT" | grep -q "$DESKTOP_ID"; then
    if [ "$SORT" = "@as []" ] || [ "$SORT" = "[]" ]; then
        sudo -u "$REAL_USER" \
            DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
            XDG_RUNTIME_DIR="$USER_RUNTIME" \
            gsettings set org.gnome.desktop.search-providers sort-order "['$DESKTOP_ID']"
    else
        NEW_SORT=$(echo "$SORT" | sed "s/\[/['$DESKTOP_ID', /")
        sudo -u "$REAL_USER" \
            DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
            XDG_RUNTIME_DIR="$USER_RUNTIME" \
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
echo "    desktop-file-validate /usr/local/share/applications/drillbit.desktop"
echo ""
echo "STEP 5 — Validate the D-Bus object is reachable after login:"
echo ""
echo "    gdbus introspect --session \\"
echo "      --dest org.drillbit.SearchProvider \\"
echo "      --object-path /org/drillbit/SearchProvider"
echo ""
echo "STEP 6 — Test it:"
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
