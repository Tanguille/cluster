# AI Serving Trials — Archive (sglang / vLLM / custom-build)

Qwen3.6-27B on a single **AMD Radeon AI PRO R9700** (gfx1201 / RDNA4, 32 GB, ROCm).
This records the SGLang and vLLM serving trials that were **removed** when the cluster
switched to **llama.cpp + MTP** (see [`sglang-rdna4-benchmarks.md`](./sglang-rdna4-benchmarks.md)
for the full bench/decision). Kept so the trials can be **revived / re-evaluated** if those
engines improve on RDNA4.

## How to get the removed build infrastructure back

### Round 2 — vLLM + SGLang custom builds removed (June 2026, PR #3414)

Custom Dockerfiles and GitHub Actions workflows for the vLLM-from-source and SGLang benchmark
builds were removed when production switched to `kyuz0/vllm-therock-gfx1201` (community build)
and SGLang remained experimental. Restore from:

```
git anchor: 5e8f36c410c120cd4ca3e86499279a5a8b872cde  (feat/vllm-cleanup, pre-removal)

# inspect:
git show 5e8f36c:docker/vllm-rocm/Dockerfile
git show 5e8f36c:docker/sglang-rocm/Dockerfile
git show 5e8f36c:docker/sglang-rocm-torch212/Dockerfile
git show 5e8f36c:.github/workflows/build-vllm-rocm.yaml
git show 5e8f36c:.github/workflows/build-sglang-rocm.yaml
git show 5e8f36c:.github/workflows/build-sglang-rocm-torch212.yaml

# or restore all at once:
git checkout 5e8f36c -- docker/vllm-rocm docker/sglang-rocm docker/sglang-rocm-torch212 \
  .github/workflows/build-vllm-rocm.yaml \
  .github/workflows/build-sglang-rocm.yaml \
  .github/workflows/build-sglang-rocm-torch212.yaml
```

- `docker/vllm-rocm/` — vLLM built from source on `rocm/vllm:rocm7.13.0_gfx120X-all`; superseded by `kyuz0/vllm-therock-gfx1201` (community build with better gfx1201 patches, faster startup, no ~19 min compile). Last pushed image: `ghcr.io/tanguille/vllm-rocm-gfx1201:gfx1201`.
- `docker/sglang-rocm/` — SGLang mainline on same ROCm base; benchmark-only (hits `gptq_marlin_repack` on load). Superseded by the mattbucci fork path — see `docs/sglang-qwen3.6-rocm-plan.md`.
- `docker/sglang-rocm-torch212/` — SGLang benchmark variant with torch 2.12.1+rocm7.2 for comparison against AMD's torch 2.11.0+rocm7.13 base.

### Round 1 — SGLang + vLLM k8s manifests removed (earlier, PR pre-history)

All trial manifests + the custom Dockerfile existed at this commit (the parent of the removal):

```
git anchor: 784f5681d055bff7892e85c646bf60380fa362f6   (branch: main, pre-removal)

# inspect or restore any removed path, e.g.:
git show 784f5681d:kubernetes/apps/ai/sglang/app/helmrelease.yaml
git checkout 784f5681d -- kubernetes/apps/ai/sglang kubernetes/apps/ai/vllm \
  docker/sglang-rdna4 \
  kubernetes/apps/actions-runner-system/actions-runner-controller/runners/gpu-builder
```

## SGLang trial (was: primary, then replaced)

- **Custom image:** `ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201@sha256:d5e8ffa7c6564f4d6d0d7c91d055783af69acdcabeae4145f8755207125cd30c`
  - Built from `docker/sglang-rdna4/` (Dockerfile + entrypoint.sh) via the `gpu-builder` GHA runner.
  - mattbucci SGLang fork, RDNA4 patches (native int4-AWQ GEMV kernel, fp8-KV patches 039/042/044).
- **Model:** `mattbucci/Qwen3.6-27B-AWQ-native-thinking-vision` (native-AWQ; plain -AWQ crashed gfx1201).
- **Key config:** AWQ int4 weights, `fp8_e4m3` KV, ctx 131072, `--attention-backend triton`,
  `--tensor-parallel-size 1`, `--max-running-requests 8`, `--mem-fraction-static 0.80`.
- **Measured:** ~14.96 tok/s decode (no MTP), ~2820 tok/s PP. RadixAttention prefix cache works,
  but **batching XOR caching are mutually exclusive** on ROCm for this hybrid.
- **Why replaced:** llama.cpp+MTP reached **34 tok/s** (2.3×) with a working prefix cache that
  *coexists* with batching. MTP in SGLang was never tested (would require re-deploying the fork).

## vLLM trial

- **Image:** `docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474`
  - NOTE: this same ROCm-7.13 / gfx120X image is the **best build base** for a custom llama.cpp
    image (its rocBLAS/hipBLASLt are gfx1201-tuned → ~2× the decode of upstream's ROCm-7.2.1 image).
- **Measured:** ~6–8 tok/s single-stream (fp8-KV unfused → Triton dequant per step), but batched to
  47 tok/s @C8 with APC prefix cache. Eliminated: single-stream too slow for the workload.

## gpu-builder (custom-image CI)

- GHA scale-to-zero runner pinned to control-1 (`runs-on: gpu-builder`), privileged, ROCm device
  access, built `ghcr.io/tanguille/sglang-rdna4`. Removed with the SGLang trial.
- Renovate rules tracked `docker/sglang-rdna4/Dockerfile` + `ghcr.io/tanguille/sglang-rdna4`
  (`.renovaterc.json5`, group `sglang-rdna4`) — also removed.
