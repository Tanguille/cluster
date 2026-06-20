# SGLang server for AMD R9700 (RDNA4 / gfx1201), for benchmarking.
#
# Previously used mattbucci/sglang (sglang-rdna4 fork) for gfx1201-tuned fp8 KV kernels, but
# that fork is private (returns 404) — CI git clone always fails. Using mainline
# sglang-project/sglang instead, installed from PyPI. Mainline ROCm support uses Triton JIT
# for attention (compiles at first run against gfx1201).
#
# Base image — why rocm/vllm for SGLang?
#   AMD does NOT publish a rocm/pytorch image for ROCm 7.13 (rocm/pytorch tops out at 7.2.4
#   without gfx1201 tuning). rocm/vllm:rocm7.13.0_gfx120X-all is the only pre-built image
#   with ROCm 7.13 + gfx1201-tuned Tensile/rocBLAS kernels + PyTorch pre-compiled for this
#   GPU. We use it as a PyTorch+ROCm base and install sglang on top.
#
# Key gfx1201 runtime settings:
#   HIP_VISIBLE_DEVICES=0     — single GPU (R9700); prevents iGPU aliasing.
#   PYTORCH_ROCM_ARCH=gfx1201 — scopes any HIP JIT compilation to gfx1201 only.

FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474

# renovate: datasource=github-releases depName=sgl-project/sglang
ARG SGLANG_VERSION=v0.5.13.post1

# Install sglang from PyPI. sglang[srt] is the server-only set — no [all] extras:
# sglang's [all] pulls flashinfer-python which is CUDA-only and fails on ROCm.
# SGLang's ROCm path uses Triton JIT for attention (JIT-compiles at first run for gfx1201).
#
# outlines_core (pulled in by sglang) has no pre-built cp313 wheel and requires Rust
# Edition 2024 to build (Rust >= 1.85). Ubuntu 24.04 ships cargo 1.75 — too old.
# Install the latest stable Rust via rustup, build sglang, then remove the toolchain so
# it doesn't bloat the final image (~800 MiB saved). The compiled .so ends up in the
# Python site-packages and is not affected by removing ~/.cargo / ~/.rustup.
#
# Triton note: sglang[srt] upgrades triton from the base image's 3.6.0+rocm7.13.0rc2
# (ROCm-patched, works on gfx1201) to 3.7.1 from PyPI (generic, crashes on gfx1201 in
# AITER's arch-detection path via triton._C). We snapshot the triton files before sglang
# installs and restore them afterward.
RUN SITE_PKG=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") \
    && tar -czf /tmp/triton-rocm.tar.gz -C "$SITE_PKG" \
         $(ls "$SITE_PKG" | grep -E '^triton(-[0-9].*dist-info)?$') \
    && apt-get update \
    && apt-get install -y --no-install-recommends libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
         | sh -s -- -y --default-toolchain stable --no-modify-path \
    && export PATH="$HOME/.cargo/bin:$PATH" \
    && pip install --no-cache-dir "sglang[srt]==${SGLANG_VERSION#v}" \
    && rm -rf "$HOME/.cargo" "$HOME/.rustup" \
    && pip uninstall -y triton \
    && tar -xzf /tmp/triton-rocm.tar.gz -C "$SITE_PKG" \
    && rm /tmp/triton-rocm.tar.gz

# sglang[srt] installs torchvision from PyPI (CUDA-only) which is incompatible with
# AMD's torch 2.11.0+rocm7.13.0rc2 in the base image. Both sglang and transformers
# import torchvision at startup, causing "operator does not exist" errors.
# --no-deps replaces the broken wheel without changing torch (preserves AMD's
# gfx1201-tuned Tensile/rocBLAS kernels and avoids ABI breaks in compiled extensions).
RUN pip install --no-cache-dir --no-deps "torchvision==0.26.0+rocm7.2" \
    --index-url https://download.pytorch.org/whl/rocm7.2

# SGLang 0.5.13 requires AMD AITER (AI Inference & Training Engine Runtime) at startup.
# AMD publishes pre-built wheels only for cp310/cp312; our base uses Python 3.13, so
# build from source against the ROCm SDK already present in the base image.
# --no-build-isolation keeps HIP/ROCm headers in scope; --no-deps avoids overriding
# torch or triton that are already tuned for gfx1201 in the base image.
RUN pip install --no-cache-dir --no-deps --no-build-isolation \
    "amd-aiter @ git+https://github.com/ROCm/aiter.git@v0.1.15.post2"

ENV HIP_VISIBLE_DEVICES=0 \
    PYTORCH_ROCM_ARCH=gfx1201

LABEL org.opencontainers.image.title="sglang-rocm-gfx1201" \
      org.opencontainers.image.description="SGLang server (mainline) for AMD RDNA4 gfx1201 — ROCm 7.13.0 gfx120X-tuned, PyTorch 2.11.0+rocm7.13.0rc2, SGLang 0.5.13" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 30000
ENTRYPOINT ["python", "-m", "sglang.launch_server"]
CMD ["--help"]
