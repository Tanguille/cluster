#!/usr/bin/env bash
#
# Build both Talos schematics from talos/schematic.yaml (multi-doc), fetch
# installer IDs from the Image Factory, and update talos/talconfig.yaml
# with the new talosImageURL for each node (first doc -> control-1, second -> control-2/3).
#
# Usage:
#   ./scripts/talos-schematics-update.sh
#   TALOS_DIR=/path/to/talos ./scripts/talos-schematics-update.sh
#
# Requires: curl, jq, yq (mise install)
#
set -euo pipefail

TALOS_DIR="${TALOS_DIR:-$(cd "$(dirname "$0")/.." && pwd)/talos}"
SCHEMATIC="${TALOS_DIR}/schematic.yaml"
TALCONFIG="${TALOS_DIR}/talconfig.yaml"

for f in "$SCHEMATIC" "$TALCONFIG"; do
  if [[ ! -f "$f" ]]; then
    echo "Error: missing $f" >&2
    exit 1
  fi
done

ID1=$(yq eval-all 'select(di == 0)' "$SCHEMATIC" | curl -sS -X POST --data-binary @- -H "Content-Type: application/x-yaml" https://factory.talos.dev/schematics | jq -r '.id')
ID2=$(yq eval-all 'select(di == 1)' "$SCHEMATIC" | curl -sS -X POST --data-binary @- -H "Content-Type: application/x-yaml" https://factory.talos.dev/schematics | jq -r '.id')

export ID1 ID2
yq e '.nodes[0].talosImageURL = "factory.talos.dev/installer/" + env(ID1) | .nodes[1].talosImageURL = "factory.talos.dev/installer/" + env(ID2) | .nodes[2].talosImageURL = "factory.talos.dev/installer/" + env(ID2)' -i "$TALCONFIG"

echo "Updated talconfig.yaml: control-1 -> $ID1, control-2/3 -> $ID2"
