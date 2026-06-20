FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474

# renovate: datasource=github-releases depName=sgl-project/sglang
ARG SGLANG_VERSION=v0.5.13.post1

# Snapshot ROCm-patched Triton (3.6.0+rocm7.13.0rc2) before sglang upgrades it to
# generic 3.7.1 from PyPI, which crashes on gfx1201 in AITER's arch-detection path.
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

# torch 2.12 variant: force-reinstall torchvision pulls torch 2.12.1+rocm7.2
RUN pip install --no-cache-dir --force-reinstall torchvision \
    --index-url https://download.pytorch.org/whl/rocm7.2

# SGLang 0.5.13 requires AMD AITER (AI Inference & Training Engine Runtime) at startup.
# No cp313 pre-built wheel — build from source against the base image's ROCm SDK.
RUN pip install --no-cache-dir --no-deps --no-build-isolation \
    "amd-aiter @ git+https://github.com/ROCm/aiter.git@v0.1.15.post2"

ENV HIP_VISIBLE_DEVICES=0 PYTORCH_ROCM_ARCH=gfx1201
EXPOSE 30000
ENTRYPOINT ["python", "-m", "sglang.launch_server"]
CMD ["--help"]

LABEL org.opencontainers.image.title="sglang-rocm-gfx1201-torch212" \
      org.opencontainers.image.description="SGLang 0.5.13 for RDNA4 gfx1201 — PyTorch 2.12.1+rocm7.2 (upstream ROCm), for torch-version comparison" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"
