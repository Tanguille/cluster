#!/usr/bin/env bash
set -euo pipefail

DOMAINS_TO_CHECK="tanguille\.site|secret_domain|my-domain\.com"

if grep -rE "$DOMAINS_TO_CHECK" kubernetes/ 2>/dev/null | grep -v "SECRET_DOMAIN\|cluster-secrets" | grep -v "\${SECRET_DOMAIN}"; then
    echo ""
    echo "ERROR: Found hardcoded domains that should use \${SECRET_DOMAIN}"
    echo "Please replace hardcoded domains with \${SECRET_DOMAIN} variable"
    echo "The \${SECRET_DOMAIN} variable is defined in cluster-secrets.sops.yaml"
    exit 1
fi
