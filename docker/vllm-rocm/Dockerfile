# vLLM OpenAI-compatible server for AMD R9700 (RDNA4 / gfx1201), built from source.
#
# Why from source instead of the AMD pre-built image:
#   rocm/vllm:rocm7.13.0_gfx120X-all ships vLLM 0.19.1; mainline vLLM is at 0.23.0.
#   The AMD image has no newer gfx1201 variant, and PyPI vLLM wheels are CUDA-only.
#   Building from source gives us the latest vLLM with ROCm compiled for gfx1201.
#
# Why Ubuntu 24.04 (not 26.04 like llama-cpp-rocm):
#   AMD's packages-multi-arch APT repo targets ubuntu2404 and only contains ROCm packages.
#   Ubuntu 26.04 ships Python 3.13+, so python3.12 is unavailable there without a third-party
#   source. Ubuntu 24.04 has python3.12 natively and matches AMD's build targets.
#
# Build chain:
#   1. Ubuntu 24.04 + AMD's gfx120X-tuned ROCm 7.13 from APT (same packages as llama-cpp-rocm)
#   2. torch==2.11.0+rocm7.2 from PyTorch's ROCm wheel index (ABI-compatible with ROCm 7.13;
#      same pairing used in the sglang-rdna4 benchmarks: torch 2.12.0+rocm7.2 on ROCm 7.13)
#   3. vLLM cloned at tag + built with VLLM_TARGET_DEVICE=rocm PYTORCH_ROCM_ARCH=gfx1201
#   4. Slim runtime stage: ROCm runtime libs + Python venv, no build toolchain
#
# gfx1201 BLAS override at runtime:
#   PyTorch's wheel bundles ROCm 7.2 BLAS without gfx1201 Tensile kernels. We override it
#   at runtime via LD_LIBRARY_PATH: the AMD APT-installed libs (gfx1201-tuned) are listed
#   first, so they win over the torch-bundled ones (LD_LIBRARY_PATH > RUNPATH/RPATH).
#
# Benchmark reference (measured on this rig with vLLM 0.19.1, 2026-06-12):
#   Single-stream decode: ~8 tok/s (fp8 KV, unfused Triton dequant — no native gfx1201 path)
#   Aggregate @C8: 47 tok/s with APC prefix caching ON (6.3× cached TTFT)

# ---------------------------------------------------------------------------------------------
# Build stage
FROM docker.io/library/ubuntu:26.04@sha256:6fee4abc5fe6a21f40022199b2d5063eb591743c80d3d2f5fadf620a98cbae0d AS build

ENV ROCM_PATH=/opt/rocm/core-7.13 \
    ROCM_HOME=/opt/rocm/core-7.13 \
    DEBIAN_FRONTEND=noninteractive

# Install ROCm 7.13 (gfx1201-tuned) from AMD's packages-multi-arch APT repo.
# Same set as llama-cpp-rocm: HIP runtime, gfx1201 Tensile/rocBLAS kernels, LLVM/clang.
# Python 3.12 is native on Ubuntu 24.04; python3.12-venv provides the venv module.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && wget -qO - https://repo.amd.com/rocm/packages/gpg/rocm.gpg \
         | gpg --dearmor | tee /etc/apt/keyrings/amdrocm.gpg > /dev/null \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://repo.amd.com/rocm/packages-multi-arch/ubuntu2404 stable main" \
         | tee /etc/apt/sources.list.d/rocm.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        amdrocm-blas7.13-gfx1201 \
        amdrocm-runtime-dev7.13 \
        amdrocm-blas-dev7.13 \
        amdrocm-hipblas-common-dev7.13 \
        amdrocm-llvm-dev7.13 \
        python3.12 python3.12-dev python3.12-venv \
        build-essential cmake ninja-build git \
    && rm -rf /var/lib/apt/lists/*

ENV HIP_PATH=${ROCM_PATH} \
    HIPCXX=${ROCM_PATH}/lib/llvm/bin/clang++ \
    PATH=${ROCM_PATH}/bin:${ROCM_PATH}/lib/llvm/bin:${PATH}

# Isolated venv so the runtime stage only needs to copy one directory.
RUN python3.12 -m venv /opt/vllm

# PyTorch 2.11.0 for ROCm 7.2 — the exact version vLLM 0.23.0 pins, from PyTorch's
# ROCm wheel index. The +rocm7.2 local tag satisfies vLLM's torch==2.11.0 constraint
# (PEP 440: local identifiers are excluded from == comparisons).
RUN /opt/vllm/bin/pip install --no-cache-dir \
    torch==2.11.0 \
    --index-url https://download.pytorch.org/whl/rocm7.2

# Latest vLLM; Renovate bumps this tag.
# renovate: datasource=github-releases depName=vllm-project/vllm
ARG VLLM_VERSION=v0.23.0
# Compile parallelism capped by the runner's ENFORCED memory limit.
# vLLM's HIP extension compilation is RAM-heavy (C++ template instantiation);
# use the same 4 GiB/job budget as llama-cpp-rocm.
ARG BUILD_JOBS=4

RUN GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "${VLLM_VERSION}" https://github.com/vllm-project/vllm /src/vllm

# Build vLLM with the HIP backend, targeting gfx1201 only.
# --no-build-isolation: use the venv's already-installed torch (avoids re-downloading CUDA torch).
# --extra-index-url: lets pip resolve ROCm variants of any remaining torch-ecosystem deps.
# MAX_JOBS + CMAKE_BUILD_PARALLEL_LEVEL: cap parallelism for the runner's memory budget.
RUN cd /src/vllm \
    && VLLM_TARGET_DEVICE=rocm \
       PYTORCH_ROCM_ARCH=gfx1201 \
       CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}" \
       MAX_JOBS="${BUILD_JOBS}" \
       GIT_TERMINAL_PROMPT=0 \
       /opt/vllm/bin/pip install --no-cache-dir --no-build-isolation \
           --extra-index-url https://download.pytorch.org/whl/rocm7.2 \
           .

# Stage the minimal ROCm runtime (same approach as llama-cpp-rocm).
# At runtime this goes on LD_LIBRARY_PATH BEFORE the torch-bundled ROCm 7.2 libs,
# ensuring the gfx1201-tuned Tensile BLAS kernels are used.
RUN mkdir -p /opt/rocm-runtime \
    && cp -a ${ROCM_PATH}/lib /opt/rocm-runtime/lib \
    && rm -rf /opt/rocm-runtime/lib/llvm/bin /opt/rocm-runtime/lib/cmake \
    && for lib in libamdhip64 librocblas libhipblaslt; do \
         test -n "$(find /opt/rocm-runtime -name "${lib}.so*" -print -quit)" \
           || { echo "FATAL: ${lib} not staged — ROCm APT layout changed"; exit 1; }; \
       done \
    && test -n "$(find /opt/rocm-runtime/lib/llvm/lib -name 'libLLVM.so*' -print -quit)" \
         || { echo "FATAL: libLLVM not staged — hipBLASLt will fail at runtime"; exit 1; }

# ---------------------------------------------------------------------------------------------
# Runtime stage — slim Ubuntu 24.04 + ROCm runtime + Python venv. No build toolchain.
FROM docker.io/library/ubuntu:26.04@sha256:6fee4abc5fe6a21f40022199b2d5063eb591743c80d3d2f5fadf620a98cbae0d AS runtime

ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 26.04 ships Python 3.13+; python3.12 is only in the AMD packages-multi-arch repo
# (ubuntu2404-targeted). We add the same repo here — not for ROCm libs (those are COPY'd from
# the build stage) but purely to install a python3.12 that matches the build-stage venv.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && wget -qO - https://repo.amd.com/rocm/packages/gpg/rocm.gpg \
         | gpg --dearmor | tee /etc/apt/keyrings/amdrocm.gpg > /dev/null \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://repo.amd.com/rocm/packages-multi-arch/ubuntu2404 stable main" \
         | tee /etc/apt/sources.list.d/rocm.list \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        python3.12 libgomp1 libstdc++6 libatomic1 libgcc-s1 libssl3 libnuma1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/vllm/         /opt/vllm/
COPY --from=build /opt/rocm-runtime/ /opt/rocm-runtime/

# AMD's gfx1201-tuned BLAS libs listed before /opt/vllm (where torch bundles its own
# ROCm 7.2 libs). LD_LIBRARY_PATH takes precedence over RUNPATH, so the Tensile-tuned
# rocBLAS/hipBLASLt kernels are loaded instead of the generic ones from the wheel.
ENV PATH=/opt/vllm/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/rocm-runtime/lib:/opt/rocm-runtime/lib/llvm/lib \
    HIP_VISIBLE_DEVICES=0 \
    VLLM_WORKER_MULTIPROC_METHOD=spawn

LABEL org.opencontainers.image.title="vllm-rocm-gfx1201" \
      org.opencontainers.image.description="vLLM built from source for AMD RDNA4 gfx1201 (ROCm 7.13 gfx120X-tuned, PyTorch 2.11.0+rocm7.2)" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 8000
ENTRYPOINT ["/opt/vllm/bin/python", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--help"]
