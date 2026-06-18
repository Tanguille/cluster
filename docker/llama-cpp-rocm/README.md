# llama-cpp-rocm-gfx1201

Custom **llama.cpp server** image for the AMD **R9700 (RDNA4 / gfx1201)**, serving
Qwen3.6-35B-A3B MoE GGUF with **MTP** (multi-token prediction). Image:
`ghcr.io/tanguille/llama-cpp-rocm-gfx1201:gfx1201`.

## Why custom

Upstream `ghcr.io/ggml-org/llama.cpp:server-rocm` ships **ROCm 7.2.1**, whose rocBLAS/hipBLASLt
are not gfx1201-tuned → **~18 tok/s**. AMD's **gfx120X-tuned ROCm 7.13** (TheRock) doubles decode
to **~34 tok/s** (measured on the prior 27B dense model). AMD now publishes the tuned gfx1201
libraries as per-arch APT packages, so the Dockerfile installs ROCm 7.13 in the build stage and
copies just the runtime libs + llama.cpp binaries into a slim final image. Current model is
**Qwen3.6-35B-A3B MoE** (UD-Q3_K_XL, ~16.0 GiB) — MoE's ~3B active params/token give faster
decode than the prior 27B dense model. See
[`docs/sglang-rdna4-benchmarks.md`](../../docs/sglang-rdna4-benchmarks.md).

## Build

GPU-less (HIP **cross-compile**) — runs on the generic `image-builder` self-hosted runner via
[`.github/workflows/build-llama-cpp-rocm.yaml`](../../.github/workflows/build-llama-cpp-rocm.yaml)
on Dockerfile changes / weekly / manual dispatch. Renovate bumps:

- `LLAMA_CPP_VERSION` (`datasource=github-releases ggml-org/llama.cpp`)
- `ROCM_VERSION` (`datasource=docker rocm/vllm`; signal-only, package names are bumped by hand)

## Runtime flags (set in `models.ini`, mounted by the HelmRelease)

```
-ngl 99 --fit off --no-mmap -fa 1 -ctk q4_0 -ctv q4_0 -c 262144 -np 1
--spec-type draft-mtp --spec-draft-n-max 2
```

- `--no-mmap` is **required**: mmap re-faults the ~16 GB weights from the PVC on cold cache,
  pushing load from 11 s to 130 s+.
- `--fit off`: we pin `-ngl`/`-c` explicitly; the auto-fit step hangs on this model.
- gfx1201 GPU access: the `squat.ai/dri` device plugin provides `/dev/dri` + `/dev/kfd` (no manual
  mount), gated by the `video`(44)/`render`(226) supplementalGroups — no privileged needed at runtime.
