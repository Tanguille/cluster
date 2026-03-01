#!/usr/bin/env bash
# Generate MCPToolConfig toolsOverride for Home Assistant MCP (strip "ha_" prefix).
# ToolHive has no regex/rewrite in MCPToolConfig, so we derive the full override
# list from ha-mcp source. Run when upgrading ha-mcp or when new tools appear.
#
# Usage: ./scripts/generate-toolhive-ha-overrides.sh [output]
#   output: "yaml" (default) = print toolsOverride YAML block
#           "list" = print one tool name per line (for hand-editing)
set -euo pipefail

HA_MCP_REF="${HA_MCP_REF:-master}"
REPO_URL="https://github.com/homeassistant-ai/ha-mcp/archive/refs/heads/${HA_MCP_REF}.tar.gz"
OUTPUT="${1:-yaml}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

echo "Fetching ha-mcp (${HA_MCP_REF})..." >&2
curl -sSL "$REPO_URL" | tar -xz -C "$workdir"

# Extract all tool names: def ha_<name>( in Python files
grep -rhoE 'def (ha_[a-z0-9_]+)\s*\(' "$workdir"/ha-mcp-*/src/ha_mcp/tools/ 2>/dev/null \
  | sed 's/def //;s/\s*($//' \
  | sort -u > "$workdir/tools.txt"

count=$(wc -l < "$workdir/tools.txt")
echo "Found ${count} tools." >&2

strip_prefix="ha_"

if [[ "$OUTPUT" == "list" ]]; then
  cat "$workdir/tools.txt"
  exit 0
fi

# Emit toolsOverride YAML (strip prefix for each). Indent for spec: (4 spaces).
echo "    # Strip prefix \"${strip_prefix}\" (server is already homeassistant). Generated from ha-mcp ${HA_MCP_REF}."
echo "    toolsOverride:"
while IFS= read -r original; do
  if [[ "$original" == "${strip_prefix}"* ]]; then
    new_name="${original#"${strip_prefix}"}"
    echo "      ${original}:"
    echo "        name: ${new_name}"
  fi
done < "$workdir/tools.txt"
