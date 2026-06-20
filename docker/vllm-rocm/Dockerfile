# vLLM OpenAI-compatible server for AMD R9700 (RDNA4 / gfx1201), for benchmarking.
#
# AMD distributes gfx120X-tuned vLLM as a pre-built image. The latest gfx1201 tag ships:
#   ROCm 7.13.0 with gfx1201-tuned Tensile/rocBLAS kernels (same stack as llama-cpp-rocm)
#   PyTorch 2.10.0  Python 3.13  Ubuntu 24.04  vLLM 0.19.1
# No source compilation needed; this Dockerfile layers only gfx1201-specific env overrides
# and the serve entrypoint on top.
#
# Key gfx1201 runtime settings:
#   HIP_VISIBLE_DEVICES=0             — single GPU (R9700); prevents iGPU aliasing.
#   VLLM_WORKER_MULTIPROC_METHOD=spawn — required for HIP multiprocessing on RDNA4.
#
# Renovate tracks this image's digest automatically via the docker manager. For a major ROCm
# version bump (e.g., 7.13.0 → 7.14.0), update the FROM tag manually.
#
# Benchmark reference (measured on this rig, 2026-06-12):
#   Single-stream decode: ~8 tok/s  (fp8 KV, unfused Triton dequant — no native gfx1201 path)
#   Aggregate @C8: 47 tok/s with APC prefix caching ON (6.3× cached TTFT)
#   Only engine doing continuous batching + prefix cache simultaneously on ROCm.

FROM docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:2255f6657d1c7d33f07054ac818ecd8d73a3284a8610066719531b3f06144ddb

ENV HIP_VISIBLE_DEVICES=0 \
    VLLM_WORKER_MULTIPROC_METHOD=spawn

LABEL org.opencontainers.image.title="vllm-rocm-gfx1201" \
      org.opencontainers.image.description="vLLM 0.19.1 OpenAI-compatible server for AMD RDNA4 gfx1201 (ROCm 7.13.0 gfx120X-tuned, PyTorch 2.10.0)" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 8000
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--help"]
