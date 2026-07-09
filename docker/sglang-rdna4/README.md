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

## Building — GPU-free, no build host requirement

The kernel C++ cross-compiles (`PYTORCH_ROCM_ARCH=gfx1201` — an explicit target, no device
probe). `setup.sh`'s GPU touches are verification-only, but it runs under `set -eo pipefail`
and one of them is a fatal `assert torch.cuda.is_available()` right after the torch install —
the Dockerfile seds that assert out (grep-guarded, like the TP=1 patches); the final device
verify already passes GPU-free (`device_count()` is 0, so the per-device loop never runs).
The build still hard-gates on `import sglang`. The real smoke test moves to deploy: the pod's
model load exercises the compiled kernels, and rollback is the previous digest.

Known GPU-less caveat: `build_skinny_gemms_int4.sh` imports its freshly-compiled `.so` — if that
import needs a device it fails with setup.sh's non-fatal WARNING and the wvSplitK **MoE** kernel
is skipped. Our `qwen36-27b` is dense and never calls it, so this is harmless for this image.

### Two-stage build (slim runtime, ~18 GB smaller)

The Dockerfile is multi-stage: the `builder` uses the ROCm **`-complete`** SDK to compile, then the
final stage starts from the slim **non-complete** ROCm base (1.2 GB vs 7.4 GB compressed) and copies
only the built conda env + fork repo. This works because torch+rocm bundles its own ROCm runtime
(~13 GB) and the compiled kernels RPATH to `torch/lib`, not `/opt/rocm` — so the ~18 GB dev SDK is
build-only. **Validation caveat:** Triton JIT-compiles at runtime and needs its ROCm backend tools
(vendored under `triton/backends/amd`). The build-time gates (`import torch, sglang`) do **not**
exercise the JIT path — so a new build must be validated
by a **cold boot with the Triton cache cleared** (`/cache/sglang/triton`) before its digest is
pinned into the HelmRelease, to prove runtime compilation still works on the slim base.

CI (`.github/workflows/build-sglang-rdna4.yaml`) builds on **`ubuntu-latest`** — zero contact
with control-1 / live serving, no maintenance window — and pushes the `v0.5.14-gfx1201` tag.
It fires on a merged change to the build inputs (a Renovate `FORK_REF` / base-digest bump;
docs excluded so a README edit can't repush the image) or on manual dispatch
(`gh workflow run build-sglang-rdna4.yaml`). Build locally with plain
`docker build docker/sglang-rdna4`. Expect ~15-30 min: the ROCm "complete" base is large and
the HIP kernels compile.

## Pin as tag@digest — the digest is authoritative

The tag gets rebuilt in place (memo `project_ik_llama_image_tags`), so the digest is what pins
the deployment — but the HelmRelease uses the repo-wide `tag: <tag>@sha256:<digest>` form, NOT a
bare `@digest`: Renovate's flux manager needs the tag present to issue digest-bump PRs.

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
