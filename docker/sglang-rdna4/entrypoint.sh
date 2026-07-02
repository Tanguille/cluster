#!/usr/bin/env bash
# Activate the conda env (which holds the SGLang install + native gfx1201 kernels),
# then hand off. Used when the image runs its default ENTRYPOINT; the sglang HelmRelease
# overrides `command` to run scripts/launch.sh, which sources common.sh and self-activates
# the same env — so both paths end up in sglang-triton36-v0514.
# No `set -u`: conda activate references unbound vars internally.
set -eo pipefail

source "${CONDA_BASE:-/opt/conda}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME:-sglang-triton36-v0514}"

# exec so the server is PID 1's child and gets SIGTERM on pod stop.
exec "$@"
