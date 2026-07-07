# sglang — Qwen3.6-27B on RDNA4

Primary inference backend for `qwen-3.6`. Serves dense **Qwen3.6-27B-AWQ** on the single
AMD R9700 (gfx1201/RDNA4, 32 GB) via the **mattbucci SGLang RDNA4 fork** (v0.5.14, torch
2.11+rocm7.2, Triton 3.6), TP=1, prefix-cache on.

Full benchmarks, the optimization ledger and the engine-selection rationale live in
[`docs/llm-hosting/`](../../../../docs/llm-hosting/).

## Config at a glance

- **Engine:** SGLang v0.5.14 (mattbucci fork @ `60ffa9501`), `qwen36-27b` preset, TP=1, KV
  dtype fp8_e4m3, mem-fraction 0.875, 48Gi pod limit (~15 GB pinned HiCache host pools + the
  AWQ shard-load transient, ~6 GB at 2 loader threads).
  mem-fraction was cut 0.90→0.875 to free ~0.8 GB VRAM for the co-resident VAAPI transcoders
  (fileflows/jellyfin) on the shared R9700; at 0.90 only ~16 MB was free and their 4K HEVC
  encodes stalled. context-length follows down (230K→200K, KV pool ~211K) — still well above
  the observed ~106K Hermes prefill peak. 200K is the floor; freeing more would mean less context.
- **Cache ON** (unified host-offload tree + HiCache host tier since 2026-07-07; was
  `MambaRadixCache`): the long-context agent re-prefills its growing context each turn, so prefix
  reuse (**~7.6× TTFT** measured pre-swap; reuse revalidated on the unified tree) is the primary
  workload. The cost is batch — overlap off, `max_running` 32→~16 (confirmed unchanged post-swap).
  On the DeltaNet hybrid, cache and high batch are mutually exclusive on RDNA4 (`extra_buffer`
  needs an FLA kernel absent on ROCm).
- **EXP-002** (`--mamba-ssm-dtype bfloat16`): halves the fp32 GatedDeltaNet state → **KV pool +87%
  (127K → 237,446 tokens)**, quality-neutral (PPL 2.1423, needle@124K PASS) — headroom that keeps
  the agent's 124K prefix from evicting under burst.
- **HiCache** (host-RAM L2, unblocked by control-1 RAM 40→64 GB): evicted KV pages + mamba states
  spill to ~15 GB of pinned host pools (`--hicache-io-backend direct`, `--hicache-ratio 1.5`) and
  reload instead of re-prefilling. The flag choices are load-bearing — the issue-by-issue rationale
  (#24121/#28434/#29034/#30314) and the validation record live in
  [`docs/llm-hosting/sglang-blockers.md`](../../../../docs/llm-hosting/sglang-blockers.md).

## Serving internals & rollback

The engine — the conda env (`sglang-triton36-v0514`, torch 2.11+rocm7.2), the native gfx1201 HIP
kernels, the fork source, and the RDNA4/TP=1 fixes the fork's stock `setup.sh` omits — is **baked
into the image** `ghcr.io/tanguille/sglang-rdna4`. The build, the pinned fork patches, and the
pin/rollback mechanics live in
[`docker/sglang-rdna4/README.md`](../../../../docker/sglang-rdna4/README.md).

The `sglang` PVC now carries only runtime data: the model (`/cache/hf`,
`mattbucci/Qwen3.6-27B-AWQ`) and the persisted Triton JIT cache (`/cache/sglang/triton`). It still
holds the pre-cutover conda env + fork source (`/cache/sglang/conda`, `/cache/sglang/repo-v0514`) as
the **rollback path** — revert the HelmRelease to the ROCm-base image and it runs from the PVC again
(rebuildable via [`scripts/sglang-env-rebuild.sh`](app/scripts/sglang-env-rebuild.sh) if lost). Those
legacy dirs are dead weight once the baked image is trusted; retirement is tracked in
`docs/llm-hosting/sglang-oci-cutover.md` step 6.

## Performance & bottleneck

Cache-on prod: ~16 tok/s single-stream decode, ~63 @conc16 aggregate, prefill ~1459 tok/s (measured
pre-HiCache; decode wall unchanged post-swap). The
end-to-end bottleneck is **prefill latency × thinking mode**, not decode: a cold 95-124K re-prefill
is 155-303s and thinking is a ~20× multiplier, while decode is a compute-bound ~13-16 tok/s wall on
the INT4/GDN kernels. One 32 GB card at TP=1 — no engine flag breaks these walls; only the prefix
cache (on) hides re-prefill cost. Full analysis in [`docs/llm-hosting/`](../../../../docs/llm-hosting/).

**Levers, by noticeable impact:**

| Lever | Layer | Status |
|---|---|---|
| **Thinking-mode routing** — latency-sensitive callers → `qwen-3.6-fast` (thinking-off) | litellm | ✅ ~20× perceived latency; routed |
| **Prefix cache** (unified tree) | sglang | ✅ 7.6× TTFT; in prod |
| **EXP-002 bf16-SSM** (+87% KV pool) | sglang | ✅ in prod |
| `--chunked-prefill` 16384 (cold prefill) | sglang | OOM-risky (prefill-activation transient); not adopted |
| cuda-graph · int8-mamba-checkpoint | sglang | tested on v0.5.14 — null / no-op on this dense + `no_buffer` config |
| **HiCache** (prompt-cache → host RAM, `--hicache-io-backend direct`) | sglang | ✅ first pass in prod (ratio 1.5, ~15 GB host pools) — node RAM 40→64 GB unblocked it |
| TP=2 / second GPU | hardware | breaks both walls (~153 tok/s w/ MTP); structural |

Bottom line: the engine wins in prod are the **prefix cache + EXP-002**; the biggest *perceived* win
is **thinking-mode routing** (client layer). Remaining sglang-config levers are noise or RAM-blocked
on this single card.
