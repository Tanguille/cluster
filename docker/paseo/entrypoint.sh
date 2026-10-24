#!/usr/bin/env bash
# Fail-closed wrapper around the upstream Paseo entrypoint.
#
# The base image only warns when PASEO_PASSWORD is unset, which leaves daemon
# control open to anyone who can reach the port. Refuse to start instead. tini
# remains PID 1 and the upstream entrypoint keeps ownership of directory setup
# and the exec of the supervisor/daemon.
set -euo pipefail

if [[ -z "${PASEO_PASSWORD:-}" ]]; then
    echo "[paseo] FATAL: PASEO_PASSWORD is missing or empty; refusing to start." >&2
    exit 1
fi

readonly UPSTREAM_ENTRYPOINT="/usr/local/bin/paseo-docker-entrypoint"
if [[ "$#" -ne 1 || "$1" != "${UPSTREAM_ENTRYPOINT}" ]]; then
    echo "[paseo] FATAL: refusing unexpected daemon command." >&2
    exit 1
fi

exec "${UPSTREAM_ENTRYPOINT}"
