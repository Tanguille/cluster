#!/usr/bin/env bash
# Evidence hooks for joryirving/pr-reviewer-action (.github/pr-review-providers.json).
# Emit JSON with severity so evidence_blocker_enforcement can map failures to request_changes.
# Uses jq and shellcheck.
#
# Note: Full-tree kubeconform is not used here — Flux/Talos HelmRelease and raw configs fail
# kubeconform even with -ignore-missing-schemas; that would block every PR. Use shellcheck
# for reliable CI-style signal; add targeted commands if you introduce dedicated validation scripts.
set -u
set -o pipefail

emit_blocker() {
  local out="$1"
  local json_msg
  json_msg=$(printf '%s' "$out" | jq -Rs .)
  printf '{"severity":"blocker","findings":%s}\n' "$json_msg"
}

case "${1:-}" in
  shellcheck)
    shopt -s nullglob
    files=(scripts/*.sh)
    if ((${#files[@]} == 0)); then
      printf '%s\n' '{"severity":"info","findings":"shellcheck: no scripts/*.sh"}'
      exit 0
    fi
    if out=$(shellcheck "${files[@]}" 2>&1); then
      printf '%s\n' '{"severity":"info","findings":"shellcheck: OK"}'
    else
      emit_blocker "$out"
    fi
    ;;
  *)
    printf '%s\n' "usage: $0 shellcheck" >&2
    exit 2
    ;;
esac
