# ROCm 10.0 image bump — plan, evidence, and the vision gate

Arm E of the `qwen38-27b-vllm` nightly-bump series, following
[vllm-nightly-bench-2026-09-29.md](vllm-nightly-bench-2026-09-29.md) (arms A–D).
That round bumped vLLM `29468dde8` → `36768d1bf` on an unchanged ROCm 7.2.3
base and found parity. This round changes the ROCm base instead.

**Status: proposed, not validated.** The manifest change is staged in a draft
PR. Nothing here has run against the GPU yet, and the sections marked
*unmeasured* are the reason it is a draft.

## What changes

| | current (`nightly`) | proposed (`nightly-rocm100`) |
|---|---|---|
| digest | `sha256:50b4f2ae…` | `sha256:bdf0a71d…` |
| vLLM commit | `36768d1bf` | `36768d1bf` — **same** |
| ROCm SDK | 7.2.3 (`rocm/dev-ubuntu-22.04:7.2.3-complete`) | 10.0.0 |
| torch | 2.13 (`PYTORCH_BRANCH=733fca1`) | **2.12.0+rocm10.0.0** |
| Triton | `669b31a` | `3.8.0+gitc0a142ff` |
| aiter | v0.1.23 | v0.1.23 — same |
| python | 3.12 | 3.12 — same |
| `PYTORCH_ROCM_ARCH` | includes `gfx1201` | includes `gfx1201` |
| `AITER_ROCM_ARCH` | `gfx942;gfx950` | `gfx942;gfx950` — same |

Digests read from the registry, not the tag list: `nightly-rocm100` currently
resolves to the same build as the pinned tag
`nightly-rocm100-36768d1bfd39094681cdbc8cb37d4b31c0729c89`, so this is the same
vLLM source on a different ROCm base. That is the point — it isolates the ROCm
bump from the vLLM bump the 09-29 round already measured.

## What this does *not* change, and why that matters

The three tuned wins on this deployment are all vLLM/aiter-side, not
ROCm-runtime-side, and all three patch targets are identical across the two
images:

- `vllm.model_executor.kernels.linear.mixed_precision.rdna_hybrid_w4a16`
  (LDS gate, +26.5% isolated warm; M≤32 Triton tile, 1.16–1.31x) — same vLLM
  commit, so the same module.
- `aiter.ops.triton.utils.unified_attention_utils` (`attn_3d` `num_stages=1`,
  64K M=1 23.25 → 29.06 tok/s) — aiter is v0.1.23 in both.
- The `/usr/local/lib/python3.12/dist-packages/` mount paths for
  `zz_lds_gate.pth` — python is 3.12 in both.

`AITER_ROCM_ARCH` is `gfx942;gfx950` in **both** images, so the AITER-on-gfx1201
story is unchanged by this bump. (An earlier note in this session suggested the
ROCm 10.0 image would break AITER selection; that was wrong — the flag is the
same either way, and the Triton attn kernels JIT for the local arch.)

## The torch downgrade

ROCm 10.0.0's validated PyTorch is 2.13.0, but the vLLM `nightly-rocm100` base
image ships **2.12.0+rocm10.0.0** — one minor version *behind* the ROCm 7.2.3
nightly this deployment runs today. That is upstream's build matrix, not
something this PR can change, and it is the single biggest reason to expect
this bump to be flat rather than faster.

This is not a pinning mistake, and there is no better ROCm 10 build to wait
for. Verified against the registry on 2026-09-30:

- `nightly` and `nightly-rocm100` cover the **identical 7 vLLM commits**, each
  pair built 1–6 minutes apart in the same pipeline run (36768d1bf: rocm7
  `05:21:01`, rocm100 `05:24:24`, 2026-09-29). The rocm100 line does not lag.
- `base-nightly-rocm100` was last rebuilt 2026-09-28 and is still pinned to
  `TORCH_VERSION=2.12.0+rocm10.0.0`, `TRITON_VERSION=3.8.0+gitc0a142ff`,
  `ROCM_SDK_VERSION=10.0.0`, `AITER_BRANCH=v0.1.23`.
- `nightly-rocm100` is the only ROCm 10 tag line. There is no
  `v0.30.0-rocm100` or any other ROCm 10 release variant — the `v0.28`–`v0.30`
  release tags are ROCm 7 only.

So torch 2.12 on ROCm 10 is a property of the published line, not a stale
digest. Re-check `base-nightly-rocm100` before running the bench; if it has
been rebuilt onto 2.13 by then, re-resolve the digest and re-run this
comparison, because a torch *upgrade* rather than a downgrade would change the
expectation from parity to a possible win at 64K prefill.

## The vision path

`FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE` is added alongside the image bump.
Scope, from `vllm/platforms/rocm.py` on `main`:

- `flash_attn_triton_available()` reads the variable and is called from exactly
  one place: the `on_gfx1x()` branch of `get_vit_attn_backend()`, which returns
  `FLASH_ATTN` instead of falling through to `TORCH_SDPA`.
- The LLM decode backend is chosen by `_get_backend_priorities()`, a different
  function that never reads this variable. `VLLM_ROCM_USE_AITER=1` still wins
  and `ROCM_AITER_UNIFIED_ATTN` still serves decode. This matches vllm#32944,
  which is where the variable was introduced.

Why it is needed rather than optional:

- vllm#49851 (open): on gfx1201 the ViT `TORCH_SDPA` path fails at model init —
  `torch.AcceleratorError: CUDA error: invalid argument` out of
  `mm_encoder_attention._forward_sdpa` → `vit_torch_sdpa_wrapper`, while
  text-only models on the same build load fine. *Caveat: that report is ROCm
  7.15, not 10.0. The specific claim "SDPA crashes on the ROCm 10.0 image" is
  unverified; what is verified is that the SDPA ViT path is unsound on gfx1201
  in general.*
- vllm#30167 (open): ROCm `flash_sdp` / `mem_efficient_sdp` produce incorrect
  vision embeddings; upstream's own recommendation is the MATH backend as
  "numerically accurate". So staying on SDPA is not the safe option either.
- Speed argues the same way: 1.8–6.5x lower ViT TTFT measured upstream on RDNA.

### The unverified part

This checkpoint's vision tower is `hidden_size 1152`, `num_heads 16` →
**head_dim 72**, non-power-of-2. That is the exact geometry behind vllm#37992,
a `Triton Error [HIP]: Code: 1, invalid argument` from
`apply_rotary_emb_flash_attn` during `profile_run` on a Qwen3.5-VL vision
tower. The issue was closed by vllm#43684 (merged 2026-06-06), which adds a
`gridY > 65535` fallback to native rotary in `ApplyRotaryEmb.forward_hip` —
that fallback is present in this build. But #43684 addresses the *grid* limit,
not head_dim 72, and upstream never confirmed the head_dim theory either way.

Two mitigating facts, neither conclusive:

- `ApplyRotaryEmb.__init__` imports `flash_attn.ops.triton.rotary`
  unconditionally on non-CPU, so the FlashAttention Triton rotary kernel is
  already in the path today, independent of this env var. If head_dim 72 were
  fatal there, the current production boot would already fail — it does not.
- The vision tower runs during `profile_run` at every boot, so a hard failure
  surfaces as a CrashLoop, not as silent degradation.

What remains genuinely open is *silent* numerical degradation, which is exactly
what no existing bench in `docs/llm-hosting/bench/` covers. Hence the next
section.

## The vision gate: `bench/visioncheck.py`

`concsweep.py`, `longctx.py` and `toolbench.py` are text-only. A model whose
ViT emits garbage passes all of them at full speed, because the vision tower is
0.92 GB of weights that only run during `profile_run` and image requests. So
"the throughput benches are green" is not evidence that photo recognition
survived.

`visioncheck.py` asks five questions with objectively checkable answers about
images it draws itself — no dataset, no network, no reference model, and the
two arms see byte-identical inputs. Categories, in the order a numerically
broken encoder fails them:

| category | probe | what it tests |
|---|---|---|
| `count` | three coloured circles | global aggregation over many patches |
| `text` | "QUAY" on a white sign | highest-frequency detail, OCR-like |
| `spatial` | red circle between two dark bands | positional encoding / rope correctness |
| `identity` | synthetic sunset | whole-image semantics |
| `color` | a yellow square | fine detail, single region |

It is a comparative gate, not an absolute one: run it on the current digest,
then on the new one, and compare the score and the per-category table. A drop
in `text` or `count` with `identity` intact is the signature of a broken
encoder rather than a merely unsure model.

Validated locally against a stub server before proposing: a healthy answer set
scores 5/5; withholding only the two fine-detail answers scores 3/5 with
`text` and `count` failing and the rest passing. A refused endpoint exits 1
with every category at 0/1, which is the vllm#49851 signature. No case accepts
a refusal string, so a blank or "I cannot tell" reply cannot pass.

### Reproducing the A/B

Follows the 09-29 method: suspend `llmkube-models` and `hermes`, scale Hermes
to 0, patch the InferenceService live, roll the pod, port-forward, then restore
with `flux resume` (git wins).

```bash
# baseline, on the current digest
kubectl -n ai patch inferenceservice qwen38-27b-vllm --type=merge \
  -p '{"spec":{"image":"vllm/vllm-openai-rocm:nightly@sha256:50b4f2aec13ffcb11ed846fec049d72250a9c2421eb1ad24c95a5210d46cee4c"}}'
kubectl -n ai rollout restart deploy -l app=llmkube

kubectl -n ai port-forward pod/<pod> 18000:8000 &
cd docs/llm-hosting/bench
python3 visioncheck.py 18000 qwen-3.8 /tmp/vision-rocm7.json
for i in 1 2 3 4 5 6; do python3 concsweep.py 18000 qwen-3.8 1,2,3,4,5; done  # first 2 discarded
python3 longctx.py 18000 qwen-3.8 4000 3
python3 longctx.py 18000 qwen-3.8 50000 3
```

Then the same sequence on the new digest with
`FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE` set, and diff the two JSON files.

### Boot checks, per arm

- All three `[lds-gate-patch]` lines still print. Their absence is silent
  throughput loss, not an error.
- `GPU KV cache size` — re-derive `maxModelLen` if the pool moved. The engine
  refuses to boot when pool < cap, and the manifest pins 246944 against a
  304,808 pool (1.23x).
- The ViT backend line. `Using Flash Attention (Triton backend) for ViT model on
  RDNA.` confirms the env var took effect; `Using Torch SDPA backend for ViT
  model.` means it did not, and the ROCm 10.0 SDPA risk is still live.
- `Using backend ... for the decoder` must stay `ROCM_AITER_UNIFIED_ATTN`. If
  the Triton ViT opt-in perturbed decoder selection, the whole tuning basis is
  invalidated and the bump is rejected on that alone.

## Decision criteria

Keep the bump only if **all** of these hold:

1. `visioncheck.py` score and per-category table are not worse than baseline.
   No category may drop by more than one item.
2. 64K decode (`longctx.py 50000`) is within noise of 29.33 tok/s.
3. 4K decode within noise of 32.5 tok/s, and M=1..5 aggregate within ~1% of
   31.3 / 56.4 / 73.7 / 97.1 / 117.2.
4. All three `[lds-gate-patch]` lines present; decoder backend still AITER.
5. KV pool re-derived if it moved, and `maxModelLen` adjusted with it.
6. No new `VLLMDecodeStepLatched` firings and no liveness restarts over the
   first 24h.

Roll back by reverting the digest and dropping the env var — both are in one
file and the compile-cache PVC survives, though the Triton/Inductor caches
under `/cache` will recompile on the next boot (~4 min, covered by the
startup probe's 240 × 15s budget).

## Unmeasured

- Whether ROCm 10.0's gfx1201 runtime is faster or slower at all. The 09-29
  arm A found a 169-commit vLLM bump worth nothing measurable; a runtime bump
  with a torch downgrade is not obviously more promising, and 64K prefill
  (59–63 s, and 87% of prompts exceed 20K) is the only phase where better
  gfx1201 GEMM kernels could plausibly show up.
- Whether FA-Triton ViT is numerically *equivalent* to SDPA on gfx1201. No
  upstream source establishes this in either direction; vllm#30167 only shows
  SDPA is broken. `visioncheck.py` is a smoke test, not an equivalence proof —
  5 cases cannot establish that. If this needs real assurance, the honest
  route is running a scored VQA set (MMStar) against both arms, which is
  outside what this repo's bench rig supports today.
- Whether the ROCm 10.0 runtime is stable on Talos `siderolabs/amdgpu`. ROCm
  10.0's matrix lists amdgpu 30.10.0–31.50.0; control-1 runs the Talos
  extension. A driver mismatch would fail at pod start rather than silently.

  Partial precedent, not proof: `kubernetes/apps/ai/llmkube/models/qwen38-paiton.yaml`
  already pins a ROCm 10 runtime for this GPU —
  `ghcr.io/eliovp/paiton-vllm-plugin:qwen38-rocm10-vllm029-…`, labelled
  `dev.paiton.runtime-rocm10=vllm029-plugin`, `PYTORCH_ROCM_ARCH=gfx1201`,
  python 3.12. So a ROCm 10 userspace has been built against gfx1201 for this
  cluster. That InferenceService is `suspend: true`, so it is not evidence the
  combination serves traffic today, and it is a different vendor image — but it
  does mean the ROCm 10 / Talos amdgpu pairing is not unexplored territory.

