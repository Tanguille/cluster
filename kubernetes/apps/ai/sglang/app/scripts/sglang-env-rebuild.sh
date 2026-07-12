#!/usr/bin/env bash
# Rebuild the SGLang RDNA4 inference env on the `sglang` PVC at /cache/sglang.
#
# WHY (do NOT just run the fork's stock scripts/setup.sh): the fork targets dual-card (TP=2);
# we serve TP=1. The stock setup.sh omits three fixes the server needs, or it crashes:
#   1. can_use_store_cache()->False — the fp8 KV store_cache JIT kernel aborts at TP=1
#      (kvcache.cuh:204, hipErrorIllegalState). WITHOUT IT: crash on the first request.
#   2. Gate the sampler's cross-TP token-id all-reduce on world_size>1 — at TP=1 it's a no-op
#      but still runs for grammar/json_schema, lazily initing NCCL (~256MB) that OOMs hours in.
#      WITHOUT IT: periodic HIP-OOM restarts on tool-calling / PR-review traffic.
#   3. `pip uninstall kernels` — transformers 5.x hub-kernels crashes `import sglang`.
# It also pins ENV_NAME / SGLANG_DIR / SGLANG_TAG (common.sh defaults point at the author's
# ephemeral /data/* paths; setup.sh defaults SGLANG_TAG to v0.5.13.post1 — wrong for our build).
#
# HOW TO RUN: bring serving down (the build needs the GPU), then run in a pod on the GPU node with
# the `sglang` PVC at /cache, same ROCm base image as the Deployment, as root:
#     kubectl scale deploy/sglang -n ai --replicas=0   # + suspend Flux
#     # launch a builder pod (rocm/dev-ubuntu-24.04:7.2.4-complete) + squat.ai/dri:1, then inside it:
#     bash sglang-env-rebuild.sh
#   Re-point the Deployment afterwards (it execs launch.sh from the PVC).
#
# NOTE: setup.sh's final verify hard-codes HIP_VISIBLE_DEVICES=0,1 and errors on a single-GPU pod —
# COSMETIC, the env is already built by then; this script applies the post-build fixes regardless.
set -uo pipefail

# v0.5.15 + the fork's own RDNA4 patch series (mattbucci's own v0.5.15 rebase as of this ref —
# no local overrides needed, see docker/sglang-rdna4/README.md). Validated 2026-07-12.
# Bump to rebase onto a newer fork HEAD (also bump SGLANG_TAG below to match the upstream version).
FORK_REF="${FORK_REF:-f9995e9d9f4157d312f9141cb466e0da2dc2e9b1}"

export ROCM_PATH=/opt/rocm
export PYTORCH_ROCM_ARCH=gfx1201
export CONDA_BASE=/cache/sglang/conda
export ENV_NAME=sglang-triton36-v0514                    # must match the fork common.sh default launch.sh activates
export REPO_DIR=/cache/sglang/repo-v0514
export SGLANG_DIR=/cache/sglang/repo-v0514/components/sglang    # PVC path (common.sh default is the ephemeral /data/sgl-v0514)
export SGLANG_TAG=v0.5.15                                 # CRITICAL: setup.sh defaults to v0.5.13.post1; the patch series only applies to the upstream tree it was built against
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

echo "=== v0.5.15: no local patch overrides needed (fork own rebase as of this FORK_REF, see docker/sglang-rdna4/README.md) ==="

echo "=== setup.sh (conda env + torch 2.11 stable + triton 3.6 + native gfx1201 kernels) ==="
cd "$REPO_DIR"
HIP_VISIBLE_DEVICES=0 ./scripts/setup.sh || echo "(ignoring setup.sh final-verify exit code — see header note)"

echo "=== TP=1 fix: force can_use_store_cache()->False (fall back to naive torch KV store) ==="
KVC="$SGLANG_DIR/python/sglang/jit_kernel/kvcache.py"
grep -q 'RDNA4 TP=1' "$KVC" || \
  sed -i '/^def can_use_store_cache(size: int) -> bool:$/a\    return False  # RDNA4 TP=1: JIT store_cache crashes (kvcache.cuh:204) -> naive torch KV store' "$KVC"

echo "=== TP=1 fix: gate the cross-TP token-id all-reduce on world size > 1 ==="
# Sampler._sync_token_ids_across_tp runs an all-reduce for grammar/structured-output
# requests even at TP=1, where it's a no-op (MIN over a 1-rank group). That first
# all-reduce lazily inits NCCL/RCCL mid-run (a ~256MB calloc); hours in, with VRAM
# committed, the calloc OOMs and crashes the engine. Gate it on a >1-rank group so
# NCCL never initialises at TP=1. WITHOUT THIS, json_schema/tool-calling traffic
# (e.g. the PR-review CI) triggers periodic HIP-OOM restarts.
SMP="$SGLANG_DIR/python/sglang/srt/layers/sampler.py"
grep -q 'get_world_size(group=self.tp_sync_group) > 1' "$SMP" || \
  sed -i 's|^        if SYNC_TOKEN_IDS_ACROSS_TP or sampling_info.grammars:$|        if (SYNC_TOKEN_IDS_ACROSS_TP or sampling_info.grammars) and dist.get_world_size(group=self.tp_sync_group) > 1:|' "$SMP"

echo "=== drop transformers-5.x 'kernels' pkg (crashes import sglang) ==="
"$CONDA_BASE/bin/conda" run -n "$ENV_NAME" pip uninstall kernels -y 2>/dev/null || true

echo "=== fix: launch.sh's --disable-cuda-graph is deprecated -> split cuda-graph-backend flags ==="
LAUNCH="$REPO_DIR/scripts/launch.sh"
grep -q -- '--disable-cuda-graph' "$LAUNCH" && \
  sed -i 's/--disable-cuda-graph/--cuda-graph-backend-decode=disabled --cuda-graph-backend-prefill=disabled/g' "$LAUNCH"

echo "=== install amdsmi (ROCm GPU-management bindings) ==="
# Without it SGLang logs "Failed to import amdsmi" at boot and falls back to torch for VRAM
# capacity detection (works, but the fallback is why mem-fraction sizing is coarser). Prefer the
# ROCm-bundled bindings at /opt/rocm/share/amd_smi so the version matches the 7.2.4 runtime/driver;
# fall back to PyPI. Non-fatal — text inference does not depend on it.
"$CONDA_BASE/bin/conda" run -n "$ENV_NAME" pip install /opt/rocm/share/amd_smi 2>/dev/null \
  || "$CONDA_BASE/bin/conda" run -n "$ENV_NAME" pip install amdsmi 2>/dev/null \
  || echo "(amdsmi install failed — non-fatal; SGLang falls back to torch VRAM detection)"

echo "=== verify ==="
# Hard gate: only declare success if sglang actually imports from the new env. setup.sh uses
# `set -uo pipefail` (no -e) and this script swallows setup.sh's exit code, so a failed patch/build
# (e.g. SGLANG_TAG mismatch -> patch reject -> env never created) would otherwise fall through to a
# misleading "REBUILD COMPLETE". Fail loudly instead.
if "$CONDA_BASE/bin/conda" run -n "$ENV_NAME" python -c "import sglang; print('sglang', sglang.__version__)"; then
  "$CONDA_BASE/bin/conda" clean -afy || true
  echo "=== REBUILD COMPLETE ==="
else
  echo "=== REBUILD FAILED: sglang did not import from $ENV_NAME (see FATAL/ABORTING above) ==="
  exit 1
fi
