# llama-cpp-rocm-gfx1201

Custom **llama.cpp server** image for the AMD **R9700 (RDNA4 / gfx1201)**, serving
Qwen3.6-27B GGUF with **MTP** (multi-token prediction). Image:
`ghcr.io/tanguille/llama-cpp-rocm-gfx1201:gfx1201`.

## Why custom

Upstream `ghcr.io/ggml-org/llama.cpp:server-rocm` ships **ROCm 7.2.1**, whose rocBLAS/hipBLASLt
are not gfx1201-tuned → **~18 tok/s**. AMD's **gfx120X-tuned ROCm 7.13** (TheRock) doubles decode
to **~34 tok/s**. That tuned stack only ships inside AMD's `rocm/vllm` gfx120X image, so we use it
as the **build base** and copy just the runtime libs + llama.cpp binaries into a slim final image
(multi-stage — the vllm/pytorch payload stays in the build stage). Measured on this rig:
`UD-Q4_K_XL` + q8_0 KV + `--spec-type draft-mtp --spec-draft-n-max 3` → **34 tok/s**, prefix
cache 7.5× warm TTFT. See [`docs/sglang-rdna4-benchmarks.md`](../../docs/sglang-rdna4-benchmarks.md).

## Build

GPU-less (HIP **cross-compile**) — runs on the generic `image-builder` self-hosted runner via
[`.github/workflows/build-llama-cpp-rocm.yaml`](../../.github/workflows/build-llama-cpp-rocm.yaml)
on Dockerfile changes / weekly / manual dispatch. Renovate bumps:

- `LLAMA_CPP_VERSION` (`datasource=github-releases ggml-org/llama.cpp`)
- `ROCM_BUILD_IMAGE` digest (`datasource=docker rocm/vllm`)

## Runtime flags (set in the HelmRelease)

```
-ngl 99 --fit off --no-mmap -fa 1 -ctk q8_0 -ctv q8_0 -c <ctx> -np <slots>
--spec-type draft-mtp --spec-draft-n-max 3
```

- `--no-mmap` is **required**: mmap re-faults the 18 GB weights from the PVC on cold cache,
  pushing load from 11 s to 130 s+.
- `--fit off`: we pin `-ngl`/`-c` explicitly; the auto-fit step hangs on this model.
- gfx1201 GPU access: the `squat.ai/dri` device plugin provides `/dev/dri` + `/dev/kfd` (no manual
  mount), gated by the `video`(44)/`render`(226) supplementalGroups — no privileged needed at runtime.
