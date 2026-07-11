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
echo "[1/4] Flux Manifest Validation..."
if command -v flate &> /dev/null; then
    if flate test all -p "${REPO_ROOT}" > /dev/null 2>&1; then
        pass "flate test all passed"
    else
        fail "flate test all found issues"
    fi
elif command -v kustomize &> /dev/null; then
    KUSTOMIZE_ERRORS=0
    while IFS= read -r -d '' ks_file; do
        app_dir=$(dirname "$ks_file")
        if ! kustomize build "$app_dir" > /dev/null 2>&1; then
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
echo "[2/4] Shell Script Validation..."
# exclude the repo-local .claude dir (session configs, worktrees) — anchored to REPO_ROOT so
# running from inside a .claude/worktrees/* worktree doesn't exclude the entire tree —
# .worktrees/ (parallel checkouts validate themselves), and
# archive/ (retired one-off scripts kept for reference; not held to the gate)
SHELL_SCRIPTS=$(find "${REPO_ROOT}" -name "*.sh" -type f -not -path "${REPO_ROOT}/.claude/*" -not -path "${REPO_ROOT}/.worktrees/*" -not -path "${REPO_ROOT}/archive/*" 2>/dev/null)
if [ -n "$SHELL_SCRIPTS" ]; then
    if command -v shellcheck &> /dev/null; then
        # shellcheck disable=SC2086 # word-splitting the list is intended; quoting it passes all paths as one filename
        if shellcheck $SHELL_SCRIPTS > /dev/null 2>&1; then
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

# Phase 3: Naming Conventions Quick Check
echo "[3/4] Naming Conventions Quick Check..."
NAMING_ERRORS=0
while IFS= read -r -d '' file; do
    # Check for files with underscores (should be dashes)
    if [[ $(basename "$file") =~ _ ]]; then
        warn "File uses underscore (use dashes): $file"
        NAMING_ERRORS=$((NAMING_ERRORS + 1))
    fi

    # Check for CamelCase in resource names (simplified check)
    if grep -qE '^[[:space:]]+name:[[:space:]]+[A-Z]' "$file" 2>/dev/null; then
        warn "Possible camelCase resource name in: $file"
        NAMING_ERRORS=$((NAMING_ERRORS + 1))
    fi
done < <(find "${REPO_ROOT}/kubernetes" -name "*.yaml" -print0 2>/dev/null)

if [ $NAMING_ERRORS -eq 0 ]; then
    pass "Naming conventions look good"
fi
echo ""

# Phase 4: Security Quick Check
echo "[4/4] Security Quick Check..."
SECURITY_ERRORS=0

# Check for hardcoded secrets (basic patterns)
while IFS= read -r -d '' file; do
    if grep -qiE '(password|secret|token|key):[[:space:]]*[^$"{[:space:]]' "$file" 2>/dev/null; then
        if ! grep -q 'sops:' "$file" 2>/dev/null; then
            warn "Possible hardcoded secret in: $file"
            SECURITY_ERRORS=$((SECURITY_ERRORS + 1))
        fi
    fi

    # Check for hardcoded domains
    if grep -qE 'example\.com|localhost|192\.168\.|10\.[0-9]+\.' "$file" 2>/dev/null; then
        warn "Possible hardcoded domain/IP in: $file"
        SECURITY_ERRORS=$((SECURITY_ERRORS + 1))
    fi
done < <(find "${REPO_ROOT}/kubernetes" -name "*.yaml" -print0 2>/dev/null)

if [ $SECURITY_ERRORS -eq 0 ]; then
    pass "No obvious security issues found"
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
