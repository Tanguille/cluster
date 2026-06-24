#!/usr/bin/env bash
# Rebuild the SGLang RDNA4 inference env on the `sglang` PVC at /cache/sglang.
#
# WHY THIS EXISTS (do NOT just run the fork's stock scripts/setup.sh):
#   The fork targets a dual-card (TP=2) box; we serve single-card (TP=1). Two
#   things the stock setup.sh does NOT do are required, or the server crashes:
#     1. store_cache TP=1 patch — the JIT store_cache kernel aborts at TP=1
#        ("kvcache.cuh:204: CUDA error: the operation cannot be performed in the
#        present state"). We force can_use_store_cache()->False so the naive torch
#        KV store is used. WITHOUT THIS THE SERVER CRASHES ON THE FIRST REQUEST.
#     2. `pip uninstall kernels` — transformers 5.x pulls a `kernels` package
#        whose hub-kernels loader crashes `import sglang`.
#   It also pins ENV_NAME / SGLANG_DIR to PVC paths (the fork's common.sh defaults
#   now point at the author's ephemeral /data/* paths) and lets setup.sh use its
#   torch 2.11.0+rocm7.2 stable default (the old 2.12 nightly pin became
#   unfetchable and faulted with expandable_segments).
#
# HOW TO RUN:
#   Bring serving down first (the build needs the GPU for kernel compiles), then
#   run this in a pod on the GPU node with the `sglang` PVC mounted at /cache,
#   using the same ROCm base image as the Deployment, as root:
#       kubectl scale deploy/sglang -n ai --replicas=0   # (+ suspend Flux; see app)
#       # launch a builder pod (rocm/dev-ubuntu-24.04:7.2.4-complete) with the PVC
#       # + squat.ai/dri:1, then inside it:
#       bash sglang-env-rebuild.sh
#   Re-point the serving Deployment afterwards (it execs launch.sh from the PVC).
#
# NOTE: setup.sh's final verify step hard-codes HIP_VISIBLE_DEVICES=0,1 and errors
# on a single-GPU pod (ROCR_VISIBLE_DEVICES=0). That error is COSMETIC — the env
# is already built by then; this script applies the post-build fixes regardless.
set -uo pipefail

# v0.5.13.post1 + 46 patches (validated 2026-06-24). Bump to rebase onto a newer fork HEAD.
FORK_REF="${FORK_REF:-1034fea9a803db43c2972cf5f74c64501db0ffd6}"

export ROCM_PATH=/opt/rocm
export PYTORCH_ROCM_ARCH=gfx1201
export CONDA_BASE=/cache/sglang/conda
export ENV_NAME=sglang-triton36-v0513                    # must match the fork common.sh default launch.sh activates
export REPO_DIR=/cache/sglang/repo
export SGLANG_DIR=/cache/sglang/repo/components/sglang    # PVC path (common.sh default is the ephemeral /data/sgl-rebase)
export TRITON_CACHE_DIR=/cache/sglang/triton
export HF_HOME=/cache/hf
export RUSTUP_HOME=/opt/rust/rustup CARGO_HOME=/opt/rust/cargo
export PATH=/opt/rust/cargo/bin:$PATH
export DEBIAN_FRONTEND=noninteractive
mkdir -p /cache/sglang "$TRITON_CACHE_DIR"

echo "=== apt deps ==="
apt-get update -qq
apt-get install -y -qq --no-install-recommends git curl ca-certificates build-essential python3-pip

echo "=== rust toolchain (build-time, for sglang grpc crate) ==="
command -v cargo >/dev/null 2>&1 || \
  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal --default-toolchain stable

echo "=== miniforge -> $CONDA_BASE ==="
if [ ! -f "$CONDA_BASE/bin/conda" ]; then
  curl -fsSL "https://github.com/conda-forge/miniforge/releases/download/24.11.3-2/Miniforge3-24.11.3-2-Linux-x86_64.sh" -o /tmp/miniforge.sh
  bash /tmp/miniforge.sh -b -p "$CONDA_BASE"; rm -f /tmp/miniforge.sh
fi

echo "=== clone fork @ $FORK_REF -> $REPO_DIR ==="
[ -d "$REPO_DIR/.git" ] || git clone https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference.git "$REPO_DIR"
git -C "$REPO_DIR" fetch origin --depth 200
git -C "$REPO_DIR" checkout "$FORK_REF"

echo "=== setup.sh (conda env + torch 2.11 stable + triton 3.6 + native gfx1201 kernels) ==="
cd "$REPO_DIR"
HIP_VISIBLE_DEVICES=0 ./scripts/setup.sh || echo "(ignoring setup.sh final-verify exit code — see header note)"

echo "=== TP=1 fix: force can_use_store_cache()->False (fall back to naive torch KV store) ==="
KVC="$SGLANG_DIR/python/sglang/jit_kernel/kvcache.py"
grep -q 'RDNA4 TP=1' "$KVC" || \
  sed -i '/^def can_use_store_cache(size: int) -> bool:$/a\    return False  # RDNA4 TP=1: JIT store_cache crashes (kvcache.cuh:204) -> naive torch KV store' "$KVC"

echo "=== drop transformers-5.x 'kernels' pkg (crashes import sglang) ==="
"$CONDA_BASE/bin/conda" run -n "$ENV_NAME" pip uninstall kernels -y 2>/dev/null || true

echo "=== verify ==="
"$CONDA_BASE/bin/conda" run -n "$ENV_NAME" python -c "import sglang; print('sglang', sglang.__version__)"
"$CONDA_BASE/bin/conda" clean -afy || true
echo "=== REBUILD COMPLETE ==="
