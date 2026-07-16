# SGLang for AMD R9700 (RDNA4 / gfx1201), serving Qwen3.6-27B int4-AWQ.
# Vendors github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference, runs its own
# scripts/setup.sh (the only build path the fork supports), then applies the TP=1 fixes
# setup.sh omits (the fork targets a dual-card TP=2 box). Re-pinning FORK_REF — and
# SGLANG_TAG on a version bump — is the whole maintenance surface. This image is the
# git-reproducible replacement for the old runtime-from-PVC env (see ../../kubernetes/
# apps/ai/sglang/README.md).
#
# Builds WITHOUT a GPU — the HIP kernels cross-compile; see the setup.sh step below and README.md.
#
# Two-stage build: the `builder` compiles the kernels + conda env with the full ROCm "complete"
# SDK; the runtime stage ships from the slim non-complete base and copies only the built env
# (~18 GB smaller). See README.md "Two-stage build" for the why (torch bundles its own ROCm
# runtime, so the dev SDK is build-only) and the Triton-JIT cold-boot validation it requires.

# ===== Stage 1: builder — full ROCm SDK, compiles the kernels + conda env =====
# Base pinned by digest — community tags get rebuilt in place (memo project_ik_llama_image_tags).
FROM docker.io/rocm/dev-ubuntu-24.04:7.2.4-complete@sha256:92f309c51b52cef8762867848f1529dee821624f23cd5df38455e819538f762f AS builder

# gfx1201 is native on ROCm 7.x — do NOT set HSA_OVERRIDE_GFX_VERSION.
# ENV_NAME / REPO_DIR / SGLANG_DIR pin the paths the fork's common.sh now defaults to the
# author's ephemeral /data/* (ENV_NAME=sglang-triton36-v0514). launch.sh sources common.sh
# and `conda activate $ENV_NAME` at runtime, so the env name baked here must match.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    ROCM_PATH=/opt/rocm \
    PYTORCH_ROCM_ARCH=gfx1201 \
    CONDA_BASE=/opt/conda \
    ENV_NAME=sglang-triton36-v0514 \
    REPO_DIR=/opt/rdna4-inference \
    SGLANG_DIR=/opt/rdna4-inference/components/sglang

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Rust via rustup stable, not apt (Ubuntu 24.04's cargo is too old for the fork's grpc crate build).
ENV RUSTUP_HOME=/opt/rust/rustup \
    CARGO_HOME=/opt/rust/cargo \
    PATH=/opt/rust/cargo/bin:${PATH}
RUN curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --profile minimal --default-toolchain stable \
    && rustc --version && cargo --version

# Miniforge: the fork's scripts are conda-coupled (conda activate $ENV_NAME).
# renovate: datasource=github-releases depName=conda-forge/miniforge
ARG MINIFORGE_VERSION=26.3.2-3
RUN curl -fsSL "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p "${CONDA_BASE}" \
    && rm /tmp/miniforge.sh

# Vendor the fork at a pinned commit (no runtime fetch).
ARG FORK_REPO=https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference.git
ARG FORK_REF=357b4b7fabb8bd1b3dfe5054ba3ac4955d49a29c
RUN git clone "${FORK_REPO}" "${REPO_DIR}" \
    && git -C "${REPO_DIR}" checkout "${FORK_REF}"

# 073 needs no override as of FORK_REF below (fork's own rebase covers it). 003 still does:
# the fork's rebased 003 only restores the common_ops guard, not the v0.5.15-added unconditional
# `infllm_v2` import — CI-verified (git apply --check against a real v0.5.15 clone) before use.
# See README.md for the full story.
COPY patches/003-rdna4-sgl-kernel-fallbacks-v0515.patch /tmp/003-rebase.patch
RUN cp /tmp/003-rebase.patch "${REPO_DIR}/patches/003-rdna4-sgl-kernel-fallbacks.patch"

# setup.sh: clone stock SGLang @ SGLANG_TAG -> RDNA4 patch series -> conda env $ENV_NAME ->
# torch 2.11.0+rocm7.2 stable (setup.sh's default; the old 2.12 nightly became unfetchable and
# faulted with expandable_segments) -> triton 3.6 -> the native gfx1201 HIP kernels.
# SGLANG_TAG override is REQUIRED: setup.sh defaults to v0.5.13.post1, and the patch series only
# applies cleanly to the upstream tree it was built against (see the FORK_REF note above).
ARG SGLANG_TAG=v0.5.15
WORKDIR ${REPO_DIR}
# GPU-free build (explicit PYTORCH_ROCM_ARCH, no device probe) — neuters setup.sh's one fatal
# GPU touch, `assert torch.cuda.is_available()` (~line 139); grep guards catch upstream moving
# it. Full rationale: README.md "Building — GPU-free".
RUN sed -i "s|assert torch.cuda.is_available(), 'CUDA not available'; ||" scripts/setup.sh \
    && ! grep -q "assert torch.cuda.is_available" scripts/setup.sh \
    && grep -qF "device_count()} GPUs" scripts/setup.sh
# conda clean in the SAME layer as the multi-GB download/solve so the pkg cache never lands in a
# committed layer — keeps the builder small on the runner's build disk (the multi-stage COPY needs
# builder + runtime bytes resident at once).
RUN SGLANG_TAG="${SGLANG_TAG}" ./scripts/setup.sh \
    && "${CONDA_BASE}/bin/conda" clean -afy

# --- TP=1 fixes the fork's setup.sh omits (it targets dual-card TP=2). Mirrors ---
# --- scripts/sglang-env-rebuild.sh. Each grep -q fails the build if the sed missed ---
# --- (e.g. an upstream rename at a new SGLANG_TAG) instead of shipping a broken image. ---
# 1. store_cache: the JIT store_cache kernel aborts at TP=1 (kvcache.cuh:204) -> force the naive torch KV store.
RUN sed -i '/^def can_use_store_cache(size: int) -> bool:$/a\    return False  # RDNA4 TP=1: JIT store_cache crashes (kvcache.cuh:204) -> naive torch KV store' \
      "${SGLANG_DIR}/python/sglang/jit_kernel/kvcache.py" \
    && grep -q 'RDNA4 TP=1' "${SGLANG_DIR}/python/sglang/jit_kernel/kvcache.py"
# 2. token-id all-reduce: gate the cross-TP sync on world>1 so NCCL never lazily inits at TP=1
#    (its first ~256MB calloc OOMs hours in once VRAM is committed -> grammar/tool-calling restarts).
RUN sed -i 's|^        if SYNC_TOKEN_IDS_ACROSS_TP or sampling_info.grammars:$|        if (SYNC_TOKEN_IDS_ACROSS_TP or sampling_info.grammars) and dist.get_world_size(group=self.tp_sync_group) > 1:|' \
      "${SGLANG_DIR}/python/sglang/srt/layers/sampler.py" \
    && grep -q 'get_world_size(group=self.tp_sync_group) > 1' "${SGLANG_DIR}/python/sglang/srt/layers/sampler.py"
# 3. transformers-5.x ships a `kernels` pkg whose hub loader crashes `import sglang`; SGLang uses its own compiled kernels.
RUN "${CONDA_BASE}/bin/conda" run -n "${ENV_NAME}" pip uninstall kernels -y 2>/dev/null || true
# 4. launch.sh's --disable-cuda-graph is deprecated (ServerArgs now wants the split
#    --cuda-graph-backend-{decode,prefill}=disabled flags) -> silences the startup warning,
#    same disabled-graph behavior.
RUN sed -i 's/--disable-cuda-graph/--cuda-graph-backend-decode=disabled --cuda-graph-backend-prefill=disabled/g' \
      "${REPO_DIR}/scripts/launch.sh" \
    && ! grep -q -- '--disable-cuda-graph' "${REPO_DIR}/scripts/launch.sh"

# amdsmi: ROCm GPU-management bindings. Without it SGLang logs "Failed to import amdsmi" and falls back
# to torch for VRAM-capacity detection (works, but coarser mem-fraction sizing on this VRAM-bound card).
# Prefer the ROCm-bundled bindings matching the 7.2.4 runtime; fall back to PyPI. Non-fatal. Mirrors env-rebuild.sh.
RUN "${CONDA_BASE}/bin/conda" run -n "${ENV_NAME}" pip install /opt/rocm/share/amd_smi 2>/dev/null \
      || "${CONDA_BASE}/bin/conda" run -n "${ENV_NAME}" pip install amdsmi 2>/dev/null \
      || echo "(amdsmi install failed — non-fatal; SGLang falls back to torch VRAM detection)"

# Hard gate: fail the build unless sglang actually imports from the patched env — a silently-missed
# patch or a swallowed setup.sh failure dies HERE.
RUN "${CONDA_BASE}/bin/conda" run -n "${ENV_NAME}" python -c "import sglang; print('sglang', sglang.__version__)"

# ===== Stage 2: runtime — slim non-complete ROCm base, only the built env copied in =====
# The non-complete base is the coherent ROCm runtime without the ~18 GB dev SDK (headers, static
# libs, docs, every math component). Pinned by digest like the builder base.
FROM docker.io/rocm/dev-ubuntu-24.04:7.2.4@sha256:bdc8e61026cbb844ede93d44d2c50055f51ebb2041906b60182bf3bee3139054

# LLMKube invokes `python3 -m sglang.launch_server` directly, bypassing this
# image's conda-activating ENTRYPOINT. Keep the patched env first in PATH.
ENV ROCM_PATH=/opt/rocm \
    CONDA_BASE=/opt/conda \
    ENV_NAME=sglang-triton36-v0514 \
    REPO_DIR=/opt/rdna4-inference \
    SGLANG_DIR=/opt/rdna4-inference/components/sglang \
    PATH=/opt/conda/envs/sglang-triton36-v0514/bin:/opt/conda/bin:${PATH}

# The built conda env (torch+rocm, triton, sglang, native gfx1201 kernels) and the fork repo
# (launch.sh, common.sh, chat template, editable sglang). Paths match the builder so the env's
# baked RPATHs/shebangs (/opt/conda/...) resolve unchanged. Build-only tooling (rust, gcc, git,
# miniforge installer, the /opt/rocm dev SDK) is left behind in the builder stage.
COPY --from=builder /opt/conda /opt/conda
COPY --from=builder /opt/rdna4-inference /opt/rdna4-inference

# Gate on the slim base: importing torch, sglang AND the compiled sgl_kernel dlopens their native
# libs, so this fails HERE if a runtime lib lived only in the dev SDK left behind in the builder.
# It does NOT exercise Triton's runtime JIT — that risk is validated at deploy (cold boot, Triton
# cache cleared).
RUN "${CONDA_BASE}/bin/conda" run -n "${ENV_NAME}" python -c "import torch, sglang, sgl_kernel; print('torch', torch.__version__, 'hip', torch.version.hip); print('sglang', sglang.__version__)"

# Correctness-critical gfx1201 env from the fork's common.sh::setup_rdna4_env. launch.sh also
# sets these at runtime (it sources common.sh) — baked here so the image is correct under any
# entrypoint. They select working code paths (AITER is CDNA-only and miscomputes on RDNA4), not tuning.
# Device-count / RCCL / TRITON_CACHE_DIR are single-vs-dual-GPU and PVC concerns -> set by the HelmRelease.
ENV SGLANG_USE_AITER=0 \
    SGLANG_USE_AITER_AR=0 \
    HIP_FORCE_DEV_KERNARG=1 \
    HSA_FORCE_FINE_GRAIN_PCIE=1 \
    GPU_MAX_HW_QUEUES=8 \
    PYTORCH_HIP_ALLOC_CONF=expandable_segments:True \
    VLLM_USE_TRITON_AWQ=1 \
    VLLM_USE_TRITON_FLASH_ATTN=1 \
    FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE \
    PYTORCH_TUNABLEOP_ENABLED=0 \
    TOKENIZERS_PARALLELISM=false \
    SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 \
    PYTHONUNBUFFERED=1

COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "sglang.launch_server", "--help"]
