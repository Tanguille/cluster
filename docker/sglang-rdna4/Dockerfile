# SGLang for AMD R9700 (RDNA4 / gfx1201), serving Qwen3.6-27B int4-AWQ.
# Vendors github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference and runs its
# own scripts/setup.sh (the only path the fork supports). Re-pinning FORK_REF is the
# whole maintenance surface. Build needs a gfx1201 GPU in the sandbox (setup.sh asserts
# torch.cuda.is_available()); built with podman, not BuildKit. See README.md.
#
# Base pinned by digest — community tags get rebuilt in place (memo project_ik_llama_image_tags).
FROM docker.io/rocm/dev-ubuntu-24.04:7.2.4-complete@sha256:92f309c51b52cef8762867848f1529dee821624f23cd5df38455e819538f762f

# gfx1201 is native on ROCm 7.x — do NOT set HSA_OVERRIDE_GFX_VERSION.
# PIP_NO_CACHE_DIR: setup.sh installs torch (~7Gi) two+ times; caching the wheels bloats the
#   build by ~15Gi for no gain (the layer is the artifact, not a dev workspace).
# MAX_JOBS: parallelise the native HIP/triton compiles across cores (the build's time floor).
ENV DEBIAN_FRONTEND=noninteractive \
    ROCM_PATH=/opt/rocm \
    PYTORCH_ROCM_ARCH=gfx1201 \
    CONDA_BASE=/opt/conda \
    ENV_NAME=sglang-triton36 \
    REPO_DIR=/opt/rdna4-inference \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MAX_JOBS=32

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Rust via rustup `stable`, not apt (Ubuntu 24.04 ships an old cargo). SGLang v0.5.12's
# grpc crate is built during `pip install -e`; Rust's back-compat means latest stable works.
ENV RUSTUP_HOME=/opt/rust/rustup \
    CARGO_HOME=/opt/rust/cargo \
    PATH=/opt/rust/cargo/bin:${PATH}
RUN curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --profile minimal --default-toolchain stable \
    && rustc --version && cargo --version

# Miniforge: the fork's scripts are conda-coupled (conda activate $ENV_NAME).
# renovate: datasource=github-releases depName=conda-forge/miniforge
ARG MINIFORGE_VERSION=24.11.3-2
RUN curl -fsSL "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p "${CONDA_BASE}" \
    && rm /tmp/miniforge.sh

# Vendor the fork at a pinned commit (no runtime fetch).
ARG FORK_REPO=https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference.git
# renovate: datasource=git-refs depName=mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference packageName=https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference branch=main
ARG FORK_REF=1592f671302030d1ec2b8851df7b7e0d2ffe18a3
RUN git clone "${FORK_REPO}" "${REPO_DIR}" \
    && git -C "${REPO_DIR}" checkout "${FORK_REF}"

# setup.sh reinstalls transformers with `pip install --no-deps "transformers>=5.0"`, which
# leaves transformers 5.6.0's own `kernels>=0.12.0,<0.13` constraint unenforced — so `kernels`
# floats to 0.15.x, where `LayerRepository.__init__` requires a revision/version that
# transformers 5.6.0 doesn't pass, crashing `import sglang` (build verify + runtime). Pin it
# back into range right after that install line, before setup.sh's verification runs.
RUN sed -i '/pip install --no-deps "transformers>=5.0" gguf/a\    pip install "kernels>=0.12.0,<0.13"' \
    "${REPO_DIR}/scripts/setup.sh"

# torch + torchvision track the latest stable rocm7.2 wheels; torchaudio has no 2.12
# wheel (unused, stays 2.11). Fall back with
# --build-arg TORCH_VERSION=2.11.0+rocm7.2 TORCHVISION_VERSION=0.26.0+rocm7.2.
ARG TORCH_VERSION=2.12.0+rocm7.2
ARG TORCHVISION_VERSION=0.27.0+rocm7.2
ARG TORCHAUDIO_VERSION=2.11.0+rocm7.2
ARG TORCH_INDEX=https://download.pytorch.org/whl/rocm7.2
# setup.sh: SGLang v0.5.12 -> RDNA4 patches -> conda env -> torch/triton/transformers
# -> native gfx1201 HIP kernels. Caches are purged in the same RUN so they don't bloat the
# layer: conda pkg tarballs, plus ~10Gi of pip wheel cache (setup.sh installs torch twice +
# our override). Trims the image from ~54Gi to ~44Gi.
WORKDIR ${REPO_DIR}
RUN TORCH_VERSION="${TORCH_VERSION}" \
    TORCHVISION_VERSION="${TORCHVISION_VERSION}" \
    TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION}" \
    TORCH_INDEX="${TORCH_INDEX}" \
    ./scripts/setup.sh \
    && "${CONDA_BASE}/bin/conda" clean -afy \
    && rm -rf /root/.cache/pip

# Correctness-critical gfx1201 env from the fork's common.sh::setup_rdna4_env — these
# select the working code paths (AITER is CDNA-only and miscomputes on RDNA4), not tuning.
# Device-count / RCCL vars are single-vs-dual-GPU concerns and live in the HelmRelease.
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
    TRITON_CACHE_DIR=/tmp/triton_cache \
    PYTHONUNBUFFERED=1

COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "sglang.launch_server", "--help"]
