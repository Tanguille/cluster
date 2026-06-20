# vLLM OpenAI-compatible server for AMD R9700 (RDNA4 / gfx1201), built from source.
#
# Why from source:
#   rocm/vllm:rocm7.13.0_gfx120X-all ships vLLM 0.19.1; mainline vLLM is at 0.23.0.
#   PyPI vLLM wheels are CUDA-only. Building on the AMD base gives us vLLM 0.23.0 with
#   the full ROCm dev toolchain already in place.
#
# Base image — same as sglang-rocm:
#   AMD does not publish a rocm/pytorch image for ROCm 7.13 with gfx1201 tuning.
#   rocm/vllm:rocm7.13.0_gfx120X-all is the only pre-built image with ROCm 7.13 +
#   gfx1201-tuned Tensile/rocBLAS kernels + PyTorch + the full ROCm dev toolchain
#   (rocrand, hipfft, hipsparse, …) needed to compile vLLM's HIP extensions.
#
# Key gfx1201 runtime settings:
#   HIP_VISIBLE_DEVICES=0     — single GPU (R9700); prevents iGPU aliasing.
#   PYTORCH_ROCM_ARCH=gfx1201 — scopes HIP JIT compilation to gfx1201 only.

FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474

# renovate: datasource=github-releases depName=vllm-project/vllm
ARG VLLM_VERSION=v0.23.0
ARG BUILD_JOBS=4

# vLLM 0.23.0 requires torch==2.11.0; base has 2.10.0.
# Uninstall the pre-built vLLM 0.19.1 first so pip doesn't get confused during the upgrade.
RUN pip uninstall -y vllm 2>/dev/null || true \
    && pip install --no-cache-dir torch==2.11.0 \
         --index-url https://download.pytorch.org/whl/rocm7.2

# All of vLLM 0.23.0's pyproject.toml build-system.requires — must be present before
# --no-build-isolation so prepare_metadata_for_build_wheel can import them.
RUN pip install --no-cache-dir \
    "packaging>=24.2" \
    "setuptools>=77.0.3,<81.0.0" \
    "setuptools-scm>=8.0" \
    "setuptools-rust>=1.9.0" \
    wheel numpy jinja2 cmake ninja

RUN GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "${VLLM_VERSION}" \
      https://github.com/vllm-project/vllm /src/vllm

# Stub amdgpu-arch so PyTorch's LoadHIP.cmake can determine the HIP architecture
# without a physical GPU. Docker build containers have no /dev/kfd; the real
# amdgpu-arch fails with "no ROCm-capable device". The stub outputs gfx1201.
RUN amdgpu_arch=$(which amdgpu-arch 2>/dev/null || echo /opt/rocm/bin/amdgpu-arch) \
    && printf '#!/bin/sh\necho gfx1201\n' > "$amdgpu_arch" \
    && chmod +x "$amdgpu_arch"

RUN cd /src/vllm \
    && VLLM_TARGET_DEVICE=rocm \
       PYTORCH_ROCM_ARCH=gfx1201 \
       CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}" \
       MAX_JOBS="${BUILD_JOBS}" \
       GIT_TERMINAL_PROMPT=0 \
       pip install --no-cache-dir --no-build-isolation \
           --extra-index-url https://download.pytorch.org/whl/rocm7.2 \
           . \
    && rm -rf /src/vllm

ENV HIP_VISIBLE_DEVICES=0 \
    PYTORCH_ROCM_ARCH=gfx1201 \
    VLLM_WORKER_MULTIPROC_METHOD=spawn

LABEL org.opencontainers.image.title="vllm-rocm-gfx1201" \
      org.opencontainers.image.description="vLLM 0.23.0 built from source for AMD RDNA4 gfx1201 — ROCm 7.13.0 gfx120X-tuned, PyTorch 2.11.0" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 8000
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--help"]
