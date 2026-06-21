# AI Serving Trials — Archive (sglang / vLLM / custom-build)

Qwen3.6-27B on a single **AMD Radeon AI PRO R9700** (gfx1201 / RDNA4, 32 GB, ROCm).
This records the SGLang and vLLM serving trials that were **removed** when the cluster
switched to **llama.cpp + MTP** (see [`sglang-rdna4-benchmarks.md`](./sglang-rdna4-benchmarks.md)
for the full bench/decision). Kept so the trials can be **revived / re-evaluated** if those
engines improve on RDNA4.

## How to get the removed build infrastructure back

### llama-cpp-rocm + image-builder removed (June 2026)

The llama.cpp custom Docker build and `image-builder` GHA runner were removed when
`kyuz0/vllm-therock-gfx1201` became production (PR #3414). Restore from:

```
git anchor: 173298878   (main, pre-removal)

git show 173298878:docker/llama-cpp-rocm/Dockerfile
git show 173298878:.github/workflows/build-llama-cpp-rocm.yaml
git show 173298878:kubernetes/apps/actions-runner-system/actions-runner-controller/runners/image-builder/helmrelease.yaml

git checkout 173298878 -- docker/llama-cpp-rocm \
  .github/workflows/build-llama-cpp-rocm.yaml \
  kubernetes/apps/actions-runner-system/actions-runner-controller/runners/image-builder
```

- `docker/llama-cpp-rocm/` — gfx1201-tuned ROCm build of llama.cpp; last image: `ghcr.io/tanguille/llama-cpp-rocm-gfx1201:gfx1201`.
- `image-builder` runner — scale-to-zero GHA runner (privileged rootful buildkitd, no GPU needed — HIP cross-compile).

### sglang / vLLM-from-source builds removed (June 2026)

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
