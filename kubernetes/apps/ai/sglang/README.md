# sglang — Qwen3.6-27B on RDNA4

Primary inference backend for `qwen-3.6`. Serves dense **Qwen3.6-27B-AWQ** on the single
AMD R9700 (gfx1201/RDNA4, 32 GB) via the **mattbucci SGLang RDNA4 fork** (v0.5.14, torch
2.11+rocm7.2, Triton 3.6), TP=1, prefix-cache on.

Full benchmarks, the optimization ledger and the engine-selection rationale live in
[`docs/llm-hosting/`](../../../../docs/llm-hosting/).

## Config at a glance

- **Engine:** SGLang v0.5.14 (mattbucci fork @ `60ffa9501`), `qwen36-27b` preset, TP=1, KV
  dtype fp8_e4m3, mem-fraction 0.90, 32Gi pod limit (the AWQ shard load peaks ~20 GB host RAM).
- **Cache ON** (`MambaRadixCache`, `no_buffer`): the long-context agent re-prefills its growing
  context each turn, so prefix reuse (**~7.6× TTFT**) is the primary workload. The cost is batch —
  overlap off, `max_running` 32→~16. On the DeltaNet hybrid, cache and high batch are mutually
  exclusive on RDNA4 (`extra_buffer` needs an FLA kernel absent on ROCm).
- **EXP-002** (`--mamba-ssm-dtype bfloat16`): halves the fp32 GatedDeltaNet state → **KV pool +87%
  (127K → 237,446 tokens)**, quality-neutral (PPL 2.1423, needle@124K PASS) — headroom that keeps
  the agent's 124K prefix from evicting under burst.

## ⚠️ Runtime-from-PVC (reproducibility debt)

The container image is **only the ROCm 7.2.4 runtime base**. The engine runs from the prebuilt
conda env on the `sglang` PVC at `/cache/sglang`:

- `/cache/sglang/conda` — env `sglang-triton36-v0514` (torch 2.11+rocm7.2, fork + native gfx1201
  HIP kernels: `sgl_kernel`, `awq_gemv`, `wvSplitK`)
- `/cache/sglang/repo-v0514` — fork source (`scripts/launch.sh`, `common.sh`, chat template) with
  the editable SGLang at `components/sglang`
- `/cache/sglang/triton` — persisted Triton JIT cache; `/cache/hf` — `mattbucci/Qwen3.6-27B-AWQ`

So serving **is not fully reproducible from git** — it depends on the PVC, rebuilt out-of-band via
[`scripts/sglang-env-rebuild.sh`](app/scripts/sglang-env-rebuild.sh), which bakes in the RDNA4/TP=1
fixes the fork's stock `setup.sh` omits (without them the server crashes on the first request, or
OOM-restarts on the first grammar request):

1. `can_use_store_cache()` → `False` — the fp8 KV `store_cache` JIT kernel hits `hipErrorIllegalState` at TP=1.
2. `sampler.py` cross-TP token-id all-reduce gated on `world_size > 1` — else grammar/json_schema
   traffic lazily inits NCCL mid-run (~256 MB) and OOM-crashes hours in.
3. `pip uninstall kernels` — transformers 5.x hub-kernels crashes `import sglang`.
4. `SGLANG_TAG=v0.5.14` — else the fork patches reject onto v0.5.13 source and the env never builds.
5. `qwen3.6_devrole_chat_template.jinja` — remaps the `developer` role and guards historical `<think>`
   blocks (correct `preserve_thinking`, no empty-arg tool-call loop).

**Follow-up (blocked):** bake the env into a versioned OCI image (`docker/sglang-rdna4/`), but the
cluster can't pull never-cached images (Spegel upstream fall-through broken), so runtime-from-PVC on
the cached ROCm base is the only path for now.

## Performance & bottleneck

Cache-on prod: ~16 tok/s single-stream decode, ~63 @conc16 aggregate, prefill ~1459 tok/s. The
end-to-end bottleneck is **prefill latency × thinking mode**, not decode: a cold 95-124K re-prefill
is 155-303s and thinking is a ~20× multiplier, while decode is a compute-bound ~13-16 tok/s wall on
the INT4/GDN kernels. One 32 GB card at TP=1 — no engine flag breaks these walls; only the prefix
cache (on) hides re-prefill cost. Full analysis in [`docs/llm-hosting/`](../../../../docs/llm-hosting/).

**Levers, by noticeable impact:**

| Lever | Layer | Status |
|---|---|---|
| **Thinking-mode routing** — latency-sensitive callers → `qwen-3.6-fast` (thinking-off) | litellm | ✅ ~20× perceived latency; routed |
| **Prefix cache** (`MambaRadixCache`) | sglang | ✅ 7.6× TTFT; in prod |
| **EXP-002 bf16-SSM** (+87% KV pool) | sglang | ✅ in prod |
| `--chunked-prefill` 16384 (cold prefill) | sglang | OOM-risky (prefill-activation transient); not adopted |
| cuda-graph · int8-mamba-checkpoint | sglang | tested on v0.5.14 — null / no-op on this dense + `no_buffer` config |
| **HiCache** (prompt-cache → host RAM, `--hicache-io-backend direct`) | sglang | `direct` path works on RDNA4 (no #24121 crash) but host-RAM-constrained on the 40 GiB node |
| TP=2 / second GPU | hardware | breaks both walls (~153 tok/s w/ MTP); structural |

Bottom line: the engine wins in prod are the **prefix cache + EXP-002**; the biggest *perceived* win
is **thinking-mode routing** (client layer). Remaining sglang-config levers are noise or RAM-blocked
on this single card.
