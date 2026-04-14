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

get_schematic_id() {
  local doc_index=$1
  yq eval-all "select(di == ${doc_index})" "$SCHEMATIC" \
    | curl -fsS --connect-timeout 10 --max-time 60 --retry 3 --retry-delay 1 --retry-all-errors \
      -X POST --data-binary @- -H "Content-Type: application/x-yaml" https://factory.talos.dev/schematics \
    | jq -r '.id'
}

ID1=$(get_schematic_id 0)
ID2=$(get_schematic_id 1)

for id in "$ID1" "$ID2"; do
  if [[ -z "$id" || "$id" == "null" ]]; then
    echo "Error: failed to retrieve Talos schematic ID from factory.talos.dev" >&2
    exit 1
  fi
done

export ID1 ID2
yq e '.nodes[0].talosImageURL = "factory.talos.dev/installer/" + env(ID1) | .nodes[1].talosImageURL = "factory.talos.dev/installer/" + env(ID2) | .nodes[2].talosImageURL = "factory.talos.dev/installer/" + env(ID2)' -i "$TALCONFIG"

echo "Updated talconfig.yaml: control-1 -> $ID1, control-2/3 -> $ID2"
