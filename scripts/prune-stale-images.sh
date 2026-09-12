#!/usr/bin/env bash
# Removes only the previous, now-superseded image IDs for Drillbit's own
# containers (drillbit_backend, drillbit_mcp-server, drillbit_ramalama).
# Never a blanket `podman image prune` — this machine hosts other unrelated
# container projects, and a system-wide prune would touch their images too.
#
# Usage: prune-stale-images.sh <old_backend_id> <old_mcp_id> <old_ramalama_id>
# Call this immediately after a rebuild, passing the image IDs each name
# pointed to *before* that rebuild. Only IDs that are no longer tagged as the
# current image (i.e. actually superseded) are removed.
set -euo pipefail

old_backend="${1:-}"
old_mcp="${2:-}"
old_ramalama="${3:-}"

new_backend=$(podman images -q localhost/drillbit_backend:latest 2>/dev/null || true)
new_mcp=$(podman images -q localhost/drillbit_mcp-server:latest 2>/dev/null || true)
new_ramalama=$(podman images -q localhost/drillbit_ramalama:latest 2>/dev/null || true)

for pair in "$old_backend:$new_backend" "$old_mcp:$new_mcp" "$old_ramalama:$new_ramalama"; do
  old_id="${pair%%:*}"
  new_id="${pair##*:}"
  if [ -n "$old_id" ] && [ "$old_id" != "$new_id" ]; then
    echo "Removing superseded Drillbit image $old_id"
    podman rmi "$old_id" >/dev/null 2>&1 || true
  fi
done
