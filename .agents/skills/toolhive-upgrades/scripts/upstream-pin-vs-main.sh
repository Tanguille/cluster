#!/usr/bin/env bash
# Fetch semver truth from upstream stacklok/toolhive and print normalized GitHub compare URLs.
# Flux/OCI pins omit "v"; GitHub tags require "v". Wrong refs break compare URLs and mislead agents.
#
# Usage (from repo root):
#   bash .agents/skills/toolhive-upgrades/scripts/upstream-pin-vs-main.sh
#   bash .agents/skills/toolhive-upgrades/scripts/upstream-pin-vs-main.sh 0.26.0
# Env: TOOLHIVE_UPSTREAM_REPO=owner/name (default stacklok/toolhive)
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPO="${TOOLHIVE_UPSTREAM_REPO:-stacklok/toolhive}"
RAW="${1:-}"

if [[ -z "${RAW}" ]]; then
  RAW="$(grep -E '^\s+tag:' "${ROOT}/kubernetes/apps/ai/toolhive/app/ocirepository.yaml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' || true)"
fi
if [[ -z "${RAW}" ]]; then
  echo "usage: $0 [x.y.z|vx.y.z]  (or ensure ${ROOT}/kubernetes/apps/ai/toolhive/app/ocirepository.yaml has ref.tag)" >&2
  exit 1
fi

PLAIN="${RAW#v}"
GH_REF="v${PLAIN}"

echo "== ToolHive upstream currency (${REPO})"
echo "OCI/Flux-style pin (semver only): ${PLAIN}"
echo "GitHub release/tag ref (leading v): ${GH_REF}"
echo

fetch_ver() {
  local url="$1"
  local code data
  code="$(curl -sS -o /tmp/toolhive-ver.$$ -w "%{http_code}" "${url}" || true)"
  if [[ "${code}" == "200" ]]; then
    data="$(tr -d '\r\n' < /tmp/toolhive-ver.$$)"
    rm -f /tmp/toolhive-ver.$$
    printf '%s' "${data}"
    return 0
  fi
  rm -f /tmp/toolhive-ver.$$
  printf '%s' "(fetch failed, HTTP ${code})"
  return 1
}

echo "-- VERSION file (semver declared in-tree; ground truth vs compare graphs) --"
MAIN_VER="$(fetch_ver "https://raw.githubusercontent.com/${REPO}/main/VERSION" || true)"
TAG_VER="$(fetch_ver "https://raw.githubusercontent.com/${REPO}/${GH_REF}/VERSION" || true)"
echo "  main:            ${MAIN_VER}"
echo "  tag ${GH_REF}: ${TAG_VER}"
echo "  your Flux pin:   ${PLAIN}"
if command -v jq >/dev/null 2>&1; then
  LR="$(curl -sS "https://api.github.com/repos/${REPO}/releases/latest" | jq -r '.tag_name // "unknown"')"
  echo "  GitHub latest release tag: ${LR}"
fi
echo

echo "-- GitHub compare (always use ${GH_REF}, never bare ${PLAIN}, on github.com URLs) --"
echo "  https://github.com/${REPO}/compare/main...${GH_REF}"
echo "  https://github.com/${REPO}/compare/${GH_REF}...main"
echo "  Interpret together with VERSION lines above — ahead/behind counts alone mislead when refs are wrong."
echo

if command -v jq >/dev/null 2>&1; then
  echo "-- REST compare/main...${GH_REF} (GitHub fields vary by workflow) --"
  curl -sS "https://api.github.com/repos/${REPO}/compare/main...${GH_REF}" | jq '{status, ahead_by, behind_by, total_commits}'
  echo "-- REST compare/${GH_REF}...main --"
  curl -sS "https://api.github.com/repos/${REPO}/compare/${GH_REF}...main" | jq '{status, ahead_by, behind_by, total_commits}'
fi
