FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474

ARG SGLANG_VERSION=v0.5.13

RUN apt-get update \
    && apt-get install -y --no-install-recommends libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
         | sh -s -- -y --default-toolchain stable --no-modify-path \
    && export PATH="$HOME/.cargo/bin:$PATH" \
    && pip install --no-cache-dir "sglang[srt]==${SGLANG_VERSION#v}" \
    && rm -rf "$HOME/.cargo" "$HOME/.rustup"

# torch 2.12 variant: force-reinstall torchvision pulls torch 2.12.1+rocm7.2
RUN pip install --no-cache-dir --force-reinstall torchvision \
    --index-url https://download.pytorch.org/whl/rocm7.2

# SGLang 0.5.13 requires 'aiter' at import time; install PyPI placeholder
RUN pip install --no-cache-dir "aiter==0.13.20191203"

ENV HIP_VISIBLE_DEVICES=0 PYTORCH_ROCM_ARCH=gfx1201
EXPOSE 30000
ENTRYPOINT ["python", "-m", "sglang.launch_server"]
CMD ["--help"]

LABEL org.opencontainers.image.title="sglang-rocm-gfx1201-torch212" \
      org.opencontainers.image.description="SGLang 0.5.13 for RDNA4 gfx1201 — PyTorch 2.12.1+rocm7.2 (upstream ROCm), for torch-version comparison" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"
