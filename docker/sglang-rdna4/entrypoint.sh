#!/usr/bin/env bash
# Activate the conda env (which holds the SGLang install + native gfx1201 kernels),
# then hand off. No `set -u`: conda activate references unbound vars internally.
set -eo pipefail

source "${CONDA_BASE:-/opt/conda}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME:-sglang-triton36}"

# exec so the server is PID 1's child and gets SIGTERM on pod stop.
exec "$@"
