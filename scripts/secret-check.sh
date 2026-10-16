#!/usr/bin/env bash
# Run through mise exec so the repository-pinned yq is available.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/secret-check.py" "$@"
