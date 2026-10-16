#!/bin/bash
# PR Validation Script - Run locally before pushing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
ERRORS=0
WARNINGS=0

echo "======================================"
echo "  GitOps PR Validation (Local)"
echo "======================================"
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() {
    echo -e "${GREEN}✓${NC} $1"
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ERRORS=$((ERRORS + 1))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

# Phase 1: Flux Manifest Validation
# flate renders every Kustomization + HelmRelease with the real Helm/Kustomize SDKs, catching
# Helm template errors a bare `kustomize build` can't see (chartRef: OCIRepository is opaque
# to kustomize) — also validates YAML syntax and duplicate keys, so no separate yaml linter.
# Falls back to kustomize build (Kustomization-only, no Helm render) if flate isn't installed.
echo "[1/3] Flux Manifest Validation..."
if command -v flate &>/dev/null; then
    if flate test all -p "${REPO_ROOT}" >/dev/null 2>&1; then
        pass "flate test all passed"
    else
        fail "flate test all found issues"
    fi
elif command -v kustomize &>/dev/null; then
    KUSTOMIZE_ERRORS=0
    while IFS= read -r -d '' ks_file; do
        app_dir=$(dirname "$ks_file")
        if ! kustomize build "$app_dir" >/dev/null 2>&1; then
            fail "kustomize build failed: $app_dir"
            KUSTOMIZE_ERRORS=$((KUSTOMIZE_ERRORS + 1))
        fi
    done < <(find "${REPO_ROOT}/kubernetes/apps" -name "kustomization.yaml" -print0 2>/dev/null)

    if [ $KUSTOMIZE_ERRORS -eq 0 ]; then
        pass "All kustomize builds passed"
    fi
else
    warn "neither flate nor kustomize installed"
fi
echo ""

# Phase 2: Shellcheck (if shell scripts exist)
echo "[2/3] Shell Script Validation..."
# Include tracked and new non-ignored scripts, never ignored clones or nested worktrees.
# Git paths are relative to REPO_ROOT, so its own path may safely contain /worktrees/.
# Use a checked producer and NUL-delimited array: filenames may contain spaces/newlines.
SHELL_LIST="$(mktemp)"
trap 'rm -f -- "$SHELL_LIST"' EXIT
SHELL_SCRIPTS=()
if git -C "${REPO_ROOT}" ls-files --cached --others --exclude-standard -z -- '*.sh' \
    ':(exclude).slim/**' ':(exclude)worktrees/**' ':(exclude).worktrees/**' \
    ':(exclude)**/worktrees/**' ':(exclude)**/.worktrees/**' >"${SHELL_LIST}"; then
    while IFS= read -r -d '' script; do
        # Deleted worktree files and symlinks are not shellcheck inputs.
        if [[ -f "${REPO_ROOT}/${script}" && ! -L "${REPO_ROOT}/${script}" ]]; then
            SHELL_SCRIPTS+=("${REPO_ROOT}/${script}")
        fi
    done <"${SHELL_LIST}"
else
    fail "Unable to discover repository shell scripts"
fi
if [ "${#SHELL_SCRIPTS[@]}" -gt 0 ]; then
    if command -v shellcheck &>/dev/null; then
        if shellcheck "${SHELL_SCRIPTS[@]}" >/dev/null 2>&1; then
            pass "shellcheck passed"
        else
            fail "shellcheck found issues"
        fi
    else
        warn "shellcheck not installed"
    fi
else
    pass "No shell scripts to check"
fi
echo ""

# Phase 3: Fail closed on secret-check/parser/test failures. No decryption or private files.
echo "[3/3] Repository Secret Guardrails..."
if (cd "${REPO_ROOT}" && bash scripts/secret-check.sh --all && python3 -m unittest discover -s scripts/tests -v); then
    pass "Secret guardrails and regression tests passed"
else
    fail "Secret guardrails or regression tests failed (run via mise exec)"
fi
echo ""

# Summary
echo "======================================"
echo "  Validation Summary"
echo "======================================"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}All checks passed!${NC} Safe to commit and push."
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}Warnings found:${NC} $WARNINGS"
    echo "Consider addressing warnings before PR."
    exit 0
else
    echo -e "${RED}Errors found:${NC} $ERRORS"
    echo -e "${YELLOW}Warnings found:${NC} $WARNINGS"
    echo ""
    echo "Fix errors before pushing."
    exit 1
fi
