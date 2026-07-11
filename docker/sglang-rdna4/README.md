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

## Pins (verified against `env-rebuild.sh`, 2026-07-11)

| Component | Pin |
|---|---|
| Base image | `rocm/dev-ubuntu-24.04:7.2.4-complete` @ `sha256:92f309c5…` |
| Fork ref | `a0445f59e9624622ca72895a34dfc1421a345881` |
| SGLang | `v0.5.15` (stock + the fork's RDNA4 patch series + 3 TP=1 fixes + 3 local v0.5.15 overrides, see below) |
| PyTorch | `2.11.0+rocm7.2` (setup.sh default; 2.12 nightly is unfetchable + faults `expandable_segments`) |
| Triton | `3.6.0` |
| conda env | `sglang-triton36-v0514` (must match the fork's `common.sh` default `launch.sh` activates) |

### The 3 TP=1 fixes baked after `setup.sh`

The fork targets a dual-card TP=2 box; we serve single-card TP=1. Without these the server
crashes on the first request or OOM-restarts hours in on the first grammar/tool-calling request:

1. `jit_kernel/kvcache.py` `can_use_store_cache() -> False` — JIT store_cache aborts at TP=1 (`kvcache.cuh:204`).
2. `srt/layers/sampler.py` — gate the cross-TP token-id all-reduce on `world > 1` (NCCL lazy-init OOM).
3. `pip uninstall kernels` — transformers-5.x's hub-kernels loader crashes `import sglang`.

### Local v0.5.15 patch overrides (ours to maintain until the fork rebases)

The fork's `main` branch hadn't rebased past v0.5.14 as of `FORK_REF` above when upstream shipped
v0.5.15. Two of the fork's 46 RDNA4 patches don't apply to the v0.5.15 tree; a third gap only
shows up mid-build. All three are handled in the Dockerfile right after the fork clone, before
`setup.sh` runs:

- **`064-ministral3-v0513-keyword-super-init.patch`**: dropped. Only touches `models/ministral3.py`,
  a file we never import (we serve `qwen36-27b`). Still broken on v0.5.15 if you serve Ministral3/Devstral.
- **`073-rdna4-mamba-extra-buffer-hip-fallback.patch`**: hand-rebased
  (`patches/073-rdna4-mamba-extra-buffer-hip-fallback-v0515.patch`). Not obsolete, just relocated —
  v0.5.15's resolution-pipeline refactor moved the mamba-cache-strategy auto-select logic from
  `server_args.py` into `arg_groups/overrides.py::_mamba_radix_cache_resolution`; same ROCm guard,
  new location.
- **`003-rdna4-sgl-kernel-fallbacks.patch`**: hand-rebased
  (`patches/003-rdna4-sgl-kernel-fallbacks-v0515.patch`). `sgl_kernel/__init__.py` grew a new
  unconditional `infllm_v2` import (v0.5.15's new CUDA-only kernels, never built by `setup_rocm.py`
  on ROCm) not covered by the original patch; guarded the same way the file already guards
  `common_ops`.

Both rebased patches were verified with `git apply --check` against the actual `v0.5.15` tag
blobs before use. Re-verify all three on the next `SGLANG_TAG` bump — drop any that the fork has
since fixed upstream. Filed as
[mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference#3](https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference/issues/3).

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
with control-1 / live serving, no maintenance window — and pushes the `v0.5.15-gfx1201` tag.
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
skopeo inspect docker://ghcr.io/tanguille/sglang-rdna4:v0.5.15-gfx1201 --format '{{.Digest}}'
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

## Multimodal: images work, video doesn't (no ffmpeg in the runtime image)

Every boot logs a ~60-line `libtorchcodec` traceback cascade (tries FFmpeg versions 4-8, all
missing `libavutil.so.5x`/`.so.6x`) while sglang tries to import 3 multimodal processors we never
use (`mimo_v2`, `mimo_v2_asr`, `mimo_audio` — a different model family). sglang itself catches
this and logs "Ignore import error when loading …" — it's non-fatal noise, not a crash, and
**left as-is** (fixing it would mean either bloating the slim runtime with unused FFmpeg libs, or
patching sglang's own multimodal module loader for a log-cosmetics-only win).

`qwen36-27b` (`model_type=qwen3_5`) reports `has_image_understanding: true` via `/get_model_info`
and IS used for image-detection workloads in prod. Traced through the actual `v0.5.15` source
(`sglang/srt/multimodal/processors/qwen_vl.py`) to confirm this noise doesn't affect that:

- **Images**: handled via `torchvision`/`PIL` directly, never touches video decoding. Confirmed
  unaffected — `qwen_vl.py`'s own import succeeds regardless of torchcodec (unlike the 3 MiMo
  processors above, which import torchcodec unconditionally at module scope with no fallback
  guard and fail to import entirely).
- **Video**: a separate code path through `VideoDecoderWrapper`
  (`sglang/srt/utils/video_decoder.py`), coded as "torchcodec preferred, decord as fallback".
  Checked the built image directly (`docker run ... python -c "import importlib.util; ..."`,
  not the live pod): `torchcodec` is installed as a Python package but its native FFmpeg `.so`s
  aren't present (hence the traceback), and `decord` isn't installed at all. **Both video
  backends are currently broken** — if video input is ever sent to this model, it will fail.
  Not fixed because nothing in prod currently sends video; if that changes, `decord` (a much
  lighter dependency than full FFmpeg/libavutil) is the natural fix — it's already coded as the
  intended fallback, just not installed.
