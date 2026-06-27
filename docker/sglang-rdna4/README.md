# sglang-rdna4 — custom SGLang image for AMD R9700 (gfx1201)

Serves **Qwen3.6-27B int4-AWQ** on a single **AMD Radeon AI PRO R9700** (RDNA4 / gfx1201)
via SGLang + the RDNA4 patch fork. This is the **git-reproducible replacement** for the
old runtime-from-PVC env (`scripts/sglang-env-rebuild.sh`); see
[`docs/llm-hosting/sglang-oci-cutover.md`](../../docs/llm-hosting/sglang-oci-cutover.md)
for the why and the cutover.

The image vendors [`mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference`](https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference)
at a pinned commit, runs its own `scripts/setup.sh` (the only build path the fork supports —
conda-only, no upstream Dockerfile), then applies the three TP=1 fixes `setup.sh` omits.
**Re-pinning `FORK_REF` (and `SGLANG_TAG` on a version bump) is the whole maintenance surface.**

> The `Dockerfile` mirrors `kubernetes/apps/ai/sglang/app/scripts/sglang-env-rebuild.sh`
> exactly (same `FORK_REF`, `SGLANG_TAG`, and the three patches). Keep them in sync — that
> script remains the emergency PVC-rebuild fallback if the registry is ever unreachable.

## Pins (verified against `env-rebuild.sh`, 2026-06-27)

| Component | Pin |
|---|---|
| Base image | `rocm/dev-ubuntu-24.04:7.2.4-complete` @ `sha256:92f309c5…` |
| Fork ref | `60ffa9501c2c6e5db628e58c7f75b727a6127ebb` |
| SGLang | `v0.5.14` (stock + the fork's RDNA4 patch series + 3 TP=1 fixes) |
| PyTorch | `2.11.0+rocm7.2` (setup.sh default; 2.12 nightly is unfetchable + faults `expandable_segments`) |
| Triton | `3.6.0` |
| conda env | `sglang-triton36-v0514` (must match the fork's `common.sh` default `launch.sh` activates) |

### The 3 TP=1 fixes baked after `setup.sh`

The fork targets a dual-card TP=2 box; we serve single-card TP=1. Without these the server
crashes on the first request or OOM-restarts hours in on the first grammar/tool-calling request:

1. `jit_kernel/kvcache.py` `can_use_store_cache() -> False` — JIT store_cache aborts at TP=1 (`kvcache.cuh:204`).
2. `srt/layers/sampler.py` — gate the cross-TP token-id all-reduce on `world > 1` (NCCL lazy-init OOM).
3. `pip uninstall kernels` — transformers-5.x's hub-kernels loader crashes `import sglang`.

## Build host requirement — needs a gfx1201 GPU

`setup.sh` asserts `torch.cuda.is_available()` and imports the freshly-compiled `.so`, so
**the build needs the R9700 exposed to the sandbox**. The kernel C++ cross-compiles
(`PYTORCH_ROCM_ARCH=gfx1201`); the verify/import steps do not. `docker build` doesn't pass
GPUs — use **podman** with device passthrough:

```bash
podman build \
  --device /dev/kfd --device /dev/dri \
  --group-add keep-groups \
  -t sglang-rdna4:local \
  docker/sglang-rdna4
```

Expect a long build (~15-20 min: the ROCm "complete" base is large; the HIP kernels compile).

## Builds — manual dispatch only

`.github/workflows/build-sglang-rdna4.yaml` builds on the **scale-to-zero `gpu-builder`
runner pinned to control-1** and pushes the `v0.5.14-gfx1201` tag. It is **`workflow_dispatch`
only** — never auto-fired by a merge or a schedule, because the build shares control-1's single
R9700 with live serving (the warning below). A Renovate `FORK_REF` / base-digest bump opens a PR;
dispatch the build by hand (`gh workflow run build-sglang-rdna4.yaml`) when you're ready for a window.

> ⚠️ The build competes with live serving for the GPU node's host RAM + VRAM. For the first
> build / a cold rebuild, **scale `sglang` to 0 first** (or run inside a serving maintenance
> window) — a build OOM can leak VRAM and wedge the node. See the cutover doc.

## Pin by digest, never the tag

Community tags get rebuilt in place (memo `project_ik_llama_image_tags`) — the sglang
HelmRelease references the **digest**, never the tag:

```bash
skopeo inspect docker://ghcr.io/tanguille/sglang-rdna4:v0.5.14-gfx1201 --format '{{.Digest}}'
```

## What's baked vs. supplied at runtime

- **Baked:** the conda env + native gfx1201 kernels, the fork repo (`launch.sh`, `common.sh`,
  the qwen3.6 chat template), and the correctness-critical gfx1201 `ENV` from
  `common.sh::setup_rdna4_env` (`SGLANG_USE_AITER=0`, Triton AWQ/flash-attn enables, …).
- **Supplied by the HelmRelease:** the `qwen36-27b` launch.sh preset + flags, `TP=1`, the
  single-GPU device vars, `CONDA_BASE=/opt/conda`, and the PVC mounts for the **model**
  (`/cache/hf`) and the **persisted Triton cache** (`/cache/sglang/triton`). The model and
  Triton cache stay on the PVC — only the engine moves into the image.

The sglang HelmRelease keeps running `scripts/launch.sh qwen36-27b …` (now from
`/opt/rdna4-inference` instead of the PVC); `launch.sh` sources `common.sh`, which activates
the conda env — so the launch config is byte-for-byte what was validated on the PVC.
