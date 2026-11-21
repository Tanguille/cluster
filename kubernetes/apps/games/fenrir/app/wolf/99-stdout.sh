#!/usr/bin/env bash
# Sourced by wolf's entrypoint as root before it drops to ubuntu, hence the subshell: set -u would
# leak into the entrypoint and the init scripts after this one.
# supervisord (now ubuntu) must reopen the container stdout and stderr for wolf's logs, and the
# fifos containerd hands out are root-only.
(
    set -euo pipefail
    chmod a+rw /dev/stdout /dev/stderr
)
