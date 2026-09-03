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

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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
echo "[1/2] Flux Manifest Validation..."
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
echo "[2/2] Shell Script Validation..."
# exclude the repo-local .claude dir (session configs, worktrees) — anchored to REPO_ROOT so
# running from inside a .claude/worktrees/* worktree doesn't exclude the entire tree — and
# .worktrees/ (parallel checkouts validate themselves)
SHELL_SCRIPTS=$(find "${REPO_ROOT}" -name "*.sh" -type f -not -path "${REPO_ROOT}/.claude/*" -not -path "${REPO_ROOT}/.worktrees/*" 2>/dev/null)
if [ -n "$SHELL_SCRIPTS" ]; then
    if command -v shellcheck &>/dev/null; then
        # shellcheck disable=SC2086 # word-splitting the list is intended; quoting it passes all paths as one filename
        if shellcheck $SHELL_SCRIPTS >/dev/null 2>&1; then
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
