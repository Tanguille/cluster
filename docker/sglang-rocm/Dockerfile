# SGLang server for AMD R9700 (RDNA4 / gfx1201), for benchmarking.
#
# Uses the mattbucci/sglang fork (sglang-rdna4), which adds native gfx1201-tuned fp8 KV
# kernels (patches 039/042/044: fused fp8_e4m3 dequant for the attention steps). Mainline
# SGLang falls back to an unfused Triton dequant path on RDNA4 — the fork's fused kernels
# deliver ~15 tok/s vs ~12 tok/s for bf16 KV, AND double the KV pool size (212K vs 105K
# tokens). Both wins come from gfx1201-native kernel dispatch in the fork.
#
# Base image — why rocm/vllm for SGLang?
#   AMD does NOT publish a rocm/pytorch image for ROCm 7.13 (rocm/pytorch tops out at 7.2.4
#   without gfx1201 tuning). rocm/vllm:rocm7.13.0_gfx120X-all is the only pre-built image
#   with ROCm 7.13 + gfx1201-tuned Tensile/rocBLAS kernels + PyTorch pre-compiled for this
#   GPU. We use it as a PyTorch+ROCm base and install sglang on top. The resident vLLM
#   packages do not conflict with sglang (sglang uses Triton for ROCm attention, not vLLM).
#
# Key gfx1201 runtime settings:
#   HIP_VISIBLE_DEVICES=0     — single GPU (R9700); prevents iGPU aliasing.
#   PYTORCH_ROCM_ARCH=gfx1201 — scopes any HIP JIT compilation to gfx1201 only.
#
# Serving configs (pass at runtime):
#   Config A — RadixAttention ON (default, no --disable-radix-cache):
#     → ~15 tok/s single-stream, 3.5× cached TTFT on shared prefixes.
#       Batching serialises on ROCm (radix-vs-overlap exclusion, server_args.py:2520).
#   Config B — --disable-radix-cache (overlap scheduling ON):
#     → ~29 tok/s aggregate @C8, TTFT ~1.2 s. No prefix cache.
#
# Benchmark reference (measured on this rig, sglang-rdna4 v0.5.12, 2026-06-12):
#   Single-stream decode: 14.96 tok/s (fp8_e4m3 KV, fused gfx1201 kernels)
#   KV pool: 212K tokens (fp8) vs 105K (bf16) — fp8 wins on both speed and capacity here.

FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474

# renovate: datasource=github-releases depName=mattbucci/sglang
ARG SGLANG_VERSION=v0.5.13

# Install sglang from the gfx1201-tuned fork. Base package only — no [all] extras:
# sglang's [all] pulls flashinfer-python which is CUDA-only and fails on ROCm.
# SGLang's ROCm path uses Triton JIT for attention (already present, JIT-compiles at
# first run against the gfx1201 target).
RUN PYTORCH_ROCM_ARCH=gfx1201 pip install --no-cache-dir \
    "git+https://github.com/mattbucci/sglang.git@${SGLANG_VERSION}"

ENV HIP_VISIBLE_DEVICES=0 \
    PYTORCH_ROCM_ARCH=gfx1201

LABEL org.opencontainers.image.title="sglang-rocm-gfx1201" \
      org.opencontainers.image.description="SGLang server (mattbucci/sglang fork) for AMD RDNA4 gfx1201 — native fp8 KV kernels, ROCm 7.13.0 gfx120X-tuned, PyTorch 2.10.0" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 30000
ENTRYPOINT ["python", "-m", "sglang.launch_server"]
CMD ["--help"]
