# sglang-rdna4 — custom SGLang image for AMD R9700 (gfx1201)

Serves **Qwen3.6-27B int4-AWQ** on a single **AMD Radeon AI PRO R9700** (RDNA4 / gfx1201)
via SGLang + the RDNA4 patch fork. See `docs/sglang-qwen3.6-rocm-plan.md` for the why.

The image vendors [`mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference`](https://github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference)
at a pinned commit and runs its own `scripts/setup.sh` — the only build path the fork
supports (conda-only, no upstream Dockerfile). Re-pinning `FORK_REF` is the whole
maintenance surface on a ROCm/SGLang bump.

## Pins (verified 2026-06-11 against the fork's `scripts/setup.sh`)

| Component | Pin |
|---|---|
| Base image | `rocm/dev-ubuntu-24.04:7.2.4-complete` @ `sha256:92f309c5…` |
| Fork ref | `1592f671302030d1ec2b8851df7b7e0d2ffe18a3` |
| SGLang | `v0.5.12` (stock, + 37 RDNA4 patches) |
| PyTorch | `2.12.0+rocm7.2` (tv `0.27.0`); torchaudio stays `2.11.0` (no 2.12 wheel, unused) — 2.11 fallback below |
| Triton | `3.6.0` (pip wheel, ROCm 7.2 channel) |
| transformers | `>=5.0` |
| Python | 3.12 (conda env `sglang-triton36`) |

## Build host requirement — needs a gfx1201 GPU

`setup.sh` asserts `torch.cuda.is_available()` and the AWQ-GEMV / skinny-GEMM build
scripts `import` the freshly-compiled `.so`, so **the build needs the R9700 exposed to
the build sandbox**. The kernel C++ itself cross-compiles (`PYTORCH_ROCM_ARCH=gfx1201`),
but the verify/import steps do not. Build on the node with the card.

`docker build` does not pass GPUs by default — use **podman** with device passthrough:

```bash
podman build \
  --device /dev/kfd --device /dev/dri \
  --group-add keep-groups \
  -t sglang-rdna4:local \
  docker/sglang-rdna4
```

Expect a long build (ROCm "complete" base is large; three HIP kernels compile).

### PyTorch version — default 2.12, fallback 2.11

The default builds **torch 2.12.0 + torchvision 0.27.0** (latest stable rocm7.2 wheels), with
torchaudio left at 2.11.0 (no 2.12 wheel exists; it's unused for text+vision serving). The fork
itself validates on torch 2.11, and its native HIP kernels compile against torch's C++ headers,
so 2.12 is one minor bump that is unvalidated against the patch set. If a kernel compile or the
smoke test fails on 2.12, fall back to the proven 2.11 triple — one flag:

```bash
podman build --device /dev/kfd --device /dev/dri --group-add keep-groups \
  --build-arg TORCH_VERSION=2.11.0+rocm7.2 \
  --build-arg TORCHVISION_VERSION=0.26.0+rocm7.2 \
  -t sglang-rdna4:torch211 docker/sglang-rdna4
```

## Automated builds

After the first manual build proves the image, builds are automated: Renovate watches this
Dockerfile (base-image digest, `FORK_REF`, miniforge) and opens a grouped `sglang-rdna4` PR;
merging it (any change under `docker/sglang-rdna4/**`) fires `.github/workflows/build-sglang-rdna4.yaml`
on the **scale-to-zero `gpu-builder` runner pinned to control-1**, which `podman build --device`s the
image and pushes the `v0.5.12-gfx1201` tag. Renovate then digest-pins the HelmRelease → Flux deploys.
The manual recipe below is for the first build / local iteration.

## Mirror to GHCR, pin by digest

Community tags get rebuilt in place (memo `project_ik_llama_image_tags`) — the
HelmRelease must reference the **digest**, never the tag.

```bash
podman tag  sglang-rdna4:local ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201
podman push ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201

# Record the pushed digest for the HelmRelease image ref:
skopeo inspect docker://ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201 --format '{{.Digest}}'
```

## What's baked vs. supplied at runtime

- **Baked (image `ENV`):** the correctness-critical gfx1201 vars from the fork's
  `common.sh::setup_rdna4_env` (`SGLANG_USE_AITER=0`, Triton AWQ/flash-attn enables,
  `PYTORCH_HIP_ALLOC_CONF`, etc.). These select the working code paths — not tuning.
- **Supplied by the HelmRelease:** model path, `--quantization awq`, `--kv-cache-dtype
  bfloat16`, context length, concurrency, and the single-GPU device vars
  (`HIP_VISIBLE_DEVICES=0`). Device-count / RCCL vars are intentionally *not* baked.

The entrypoint activates the `sglang-triton36` conda env and `exec`s the CMD, so the
HelmRelease passes `python -m sglang.launch_server …` as args (see Step 3 of the plan).
