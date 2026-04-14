#!/usr/bin/env bash
# Heuristic rg audit for ToolHive manifests under this repo.
# Run from workspace root: bash .agents/skills/toolhive-upgrades/scripts/audit-toolhive-yaml.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
TH="${ROOT}/kubernetes/apps/ai/toolhive"

if [[ ! -d "$TH" ]]; then
  echo "Expected directory missing: $TH" >&2
  exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "rg (ripgrep) is required" >&2
  exit 1
fi

echo "== ToolHive manifest audit: $TH"
echo

echo "-- Bare scalar groupRef (v0.20+ use groupRef: then name: <group>) --"
# Matches one-line "  groupRef: my-group" but not "  groupRef:" alone (struct form).
if rg -n '^\s+groupRef:\s+\S' "$TH" --glob '*.yaml'; then
  :
else
  echo "(none)"
fi
echo

echo "-- Old camelCase remoteURL (v0.19+ use remoteUrl) --"
rg -n '\bremoteURL\b' "$TH" --glob '*.yaml' || echo "(none)"
echo

echo "-- Removed / risky MCPRegistry / enforcement --"
rg -n 'enforceServers|\bremoteURL\b|\bexternalURL\b' "$TH" --glob '*.yaml' || echo "(none)"
echo

echo "-- Deprecated MCPServer spec.port / targetPort (v0.15+; config/ only — excludes HTTPRoute) --"
rg -n '^\s+(port|targetPort):\s*[0-9]' "$TH/config" --glob '*.yaml' || echo "(none)"
echo

echo "-- Plaintext clientSecret in toolhive tree (should use refs) --"
rg -n 'clientSecret:\s*[^R{]' "$TH" --glob '*.yaml' || echo "(none)"
echo

echo "-- Inline groupRef under spec.config (v0.20: prefer spec.groupRef.name) --"
rg -n '^\s{4}groupRef:\s+\S' "$TH" --glob '**/virtualmcpservers.yaml' || echo "(none)"
echo

echo "Done. Triage each hit against the target ToolHive release notes."
