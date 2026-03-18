#!/bin/bash
# PR Validation Script - Run locally before pushing
# 2026 GitOps best practices: https://oneuptime.com/blog/post/2026-03-06-implement-gitops-pull-request-validation-flux-cd

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
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

# Phase 1: YAML Syntax Validation
echo "[1/6] YAML Syntax Validation..."
if command -v yamllint &> /dev/null; then
    if yamllint -c "${REPO_ROOT}/.yamllint.yaml" "${REPO_ROOT}/kubernetes/" > /dev/null 2>&1; then
        pass "yamllint passed"
    else
        fail "yamllint found issues (run: yamllint kubernetes/)"
    fi
else
    warn "yamllint not installed (mise install to use yamllint)"
fi
echo ""

# Phase 2: Kubernetes Schema Validation
echo "[2/6] Kubernetes Schema Validation..."
if command -v kubeconform &> /dev/null; then
    # Download Flux CRD schemas if not present
    FLUX_SCHEMAS="/tmp/flux-schemas"
    if [ ! -d "$FLUX_SCHEMAS" ]; then
        echo "  Downloading Flux CRD schemas..."
        mkdir -p "$FLUX_SCHEMAS"
        curl -sL https://github.com/fluxcd/flux2/releases/latest/download/crd-schemas.tar.gz | \
            tar xz -C "$FLUX_SCHEMAS" 2>/dev/null || true
    fi
    
    if find "${REPO_ROOT}/kubernetes" -name "*.yaml" -type f -print0 | \
        xargs -0 kubeconform -strict -ignore-missing-schemas \
            -schema-location default \
            -schema-location "${FLUX_SCHEMAS}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json" \
            > /dev/null 2>&1; then
        pass "kubeconform passed"
    else
        fail "kubeconform found schema issues"
    fi
else
    warn "kubeconform not installed"
fi
echo ""

# Phase 3: Kustomize Build Validation
echo "[3/6] Kustomize Build Validation..."
if command -v kustomize &> /dev/null; then
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
    warn "kustomize not installed"
fi
echo ""

# Phase 4: Shellcheck (if shell scripts exist)
echo "[4/6] Shell Script Validation..."
SHELL_SCRIPTS=$(find "${REPO_ROOT}" -name "*.sh" -type f 2>/dev/null | head -20)
if [ -n "$SHELL_SCRIPTS" ]; then
    if command -v shellcheck &> /dev/null; then
        if shellcheck "$SHELL_SCRIPTS" > /dev/null 2>&1; then
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

# Phase 5: Naming Conventions Quick Check
echo "[5/6] Naming Conventions Quick Check..."
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

# Phase 6: Security Quick Check
echo "[6/6] Security Quick Check..."
SECURITY_ERRORS=0

# Check for hardcoded secrets (basic patterns)
while IFS= read -r -d '' file; do
    if grep -qiE '(password|secret|token|key):[[:space:]]*[^$"{]' "$file" 2>/dev/null; then
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
