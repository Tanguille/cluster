# RDNA4 (R9700/gfx1201) Engine Comparison — INTERIM Decision Aid

Serving target: **Qwen3.6-27B** (Qwen3-Next-family GatedDeltaNet hybrid — 64 layers =
16 full-attention + 48 GDN linear-attention, `full_attention_interval=4`) on a **single
AMD Radeon AI PRO R9700** (gfx1201/RDNA4, 32 GB, ROCm 7.x, TP=1).

> **Status: INTERIM checkpoint.** Two on-hardware tests are still pending: **llama.cpp**
> decode-speed measurement and the **Hipfire** on-hardware run. Numbers from already-run
> engines are MEASURED and used verbatim. Sources:
> [`sglang-rdna4-benchmarks.md`](./sglang-rdna4-benchmarks.md) (measured sglang + vLLM on
> this rig) and [`amd-rdna4-inference-engines-research.md`](./amd-rdna4-inference-engines-research.md)
> (engine-by-engine architecture/AMD-support research).

---

## 1. TL;DR — interim recommendation

For the user's profile — **agentic tool-calling, usually only a few concurrent sessions
(not 10), needs ≥1 session at full 131K, wants strong prefix/prompt caching for agentic
reuse + KV compression, and has flagged single-stream decode SPEED as a priority** — the
two real contenders today are the **sglang fork** (15 tok/s single-stream via native fused
gfx1201 fp8-KV kernels, working RadixAttention prefix cache, fits 131K — *but batching XOR
caching are mutually exclusive on ROCm*) and **vLLM** (the only engine doing
batching + APC prefix cache + 131K + fp8-KV compression all at once: 47 tok/s aggregate
@C8, 6.3× cached TTFT — *but only ~6–8 tok/s single-stream*). Because the user weights
**single-stream speed highly and runs few concurrent sessions**, the interim pick is the
**sglang fork in Config A (radix cache ON)**: it is 2× faster per stream (15 vs 8 tok/s),
delivers a 3.5× cached-TTFT agentic win, and fits 131K — at low concurrency its inability
to batch is largely irrelevant. **vLLM is the standby** the moment "a few sessions" starts
behaving like real concurrent load (it scales to 47 tok/s *while keeping the cache hot*,
which sglang structurally cannot). **The verdict is held open pending the llama.cpp and
Hipfire results** (Section 6).

---

## 2. Master comparison — engines that CAN physically run on gfx1201 today

All numbers are single-GPU (TP=1), model `Qwen3.6-27B`, random in2048/out256 unless noted.
MEASURED = run on this exact rig. PENDING = test not yet executed (expected numbers cited).

| Engine | Single-stream decode | Batching behavior | Prefix cache (works for THIS hybrid?) | KV compression | Full 131K ctx | Maturity / risk | Status |
|---|---|---|---|---|---|---|---|
| **sglang fork** (`sglang-rdna4 v0.5.12`, AWQ-int4, fp8 KV) | **14.96 tok/s** (TPOT 63 ms) — native fused gfx1201 fp8-KV kernel | **Batching XOR caching** on ROCm. Config A (radix on): collapses to ~9 tok/s @C4/C8, TTFT explodes to 80 s. Config B (`--disable-radix`): scales to **28.81 tok/s @C8** (peak 40), TTFT ~1.2 s — but no cache | **Yes (RadixAttention)** — 3.5× cached TTFT (6.62 s cold → 1.86–2.13 s warm on 7.5K prefix). **But only in Config A; disabled the moment you batch** | **fp8_e4m3 fused** — wins here (faster *and* 2× pool: 212K-token pool vs bf16 105K). bf16 KV slower (12.35) & caps ~105K | **Yes** (212K fp8 pool) | Prod-validated on this rig; mattbucci fork; needs torch 2.12 + `PYTORCH_HIP_ALLOC_CONF=""` allocator fix. **Lowest risk** | **MEASURED** |
| **vLLM** (`0.19.1+rocm7.13`, GPTQ-4bit, fp8 KV, APC on) | **~6–8 tok/s** (fp8-KV path unfused → Triton dequant every step). C1 sweep = 8.43 tok/s | **Scales 5.6×:** 8.43→**47.04 tok/s @C8**→46.74 @C10 (saturates, compute-bound). TTFT 1.4–3.3 s under load, TPOT 114→199 ms. **Beats sglang's best (28.8) AND keeps cache on** | **Yes (APC)** — **6.3× cached TTFT** (5.81 s cold → 0.92 s warm on ~6K prefix). **Coexists with batching** (confirmed: C8 shared-prefix run 36.8 tok/s with APC on, no collapse) | **fp8 KV — REQUIRED** to fit 131K (fp16 KV leaves only ~7 GiB after 19.2 GiB weights+vision). KV pool 8.33 GiB, max-conc @131K = 1.93× | **Yes** (only via fp8 KV; fp16 KV can't fit even one 131K req) | Mainline vLLM + ROCm 7.13; the **only engine doing batch+APC+131K+KV-compression simultaneously** on this GPU. Low risk | **MEASURED** |
| **llama.cpp** (C++, ROCm/HIP; GGUF, KV q8/q4) | **PENDING** — expected **~42–48 tok/s** single-stream (the *only* engine that might beat sglang per-stream). **KEY RISK: not perf-tuned** ("CORRECTNESS ONLY" per merge PR #16095) | **Static-slot batching; BROKEN on ROCm for hybrid** — 1st req OK, 2nd (6,463-tok) req triggers a kernel error during batch processing (#19518) | **NO / BROKEN** — `cache_reuse` unsupported for hybrid → "forcing full prompt re-processing" (#21383). Worse: **illegal-memory-access crash** during prompt-cache save under agentic ~29K rapidly-changing prefixes (#21383) | KV q8/q4 (generic, not hybrid-aware) | Yes (loads 49/49 layers; arch merged #16095) | Arch runs, but **prefix cache broken + batching crash + agentic-load crash + untuned**. Higher operational risk than sglang | **PENDING (test: decode tok/s)** |
| **Hipfire** (Rust, gfx1201-NATIVE; `.mq4`, asym3 KV + q8 DeltaNet state) | **PENDING** — expected **~44 tok/s** (author number, on a 7900 XTX; gfx1201-native WMMA kernels, author runs 4× R9700). **Could be the per-stream winner** | **NONE — serial** (no continuous batching anywhere in codebase, HIGH confidence). Concurrent sessions serialize | **Weak / not a general system** — mentioned once, model-specific; first scout overstated it. Not a real radix/APC cache | **Yes** — asym3 KV + q8 error-feedback DeltaNet-state quant (real, gfx1201-native) | Likely (gfx1201-native, designed for this model) | **Alpha, single-author, AI-built, no GPU CI, all RDNA4 perf self-reported, proprietary `.mq4/.hf4`.** "Promising-but-unproven." The thing to watch | **PENDING (on-hardware test)** |

**Reading the table:** sglang and vLLM are the two MEASURED, low-risk options and they
sit at opposite ends of one axis — **sglang = fastest single stream (15 tok/s) but
batching XOR caching; vLLM = both-at-once + 47 tok/s aggregate but slow per-stream (8).**
llama.cpp and Hipfire are the two PENDING wildcards that *could* beat both on raw
single-stream decode (~42–48 / ~44 tok/s) — but each carries a disqualifying-or-worrying
caveat (llama.cpp: broken cache + crashes; Hipfire: no batching + alpha) that single-stream
speed alone may not redeem given the user still wants caching.

---

## 3. Engines that CANNOT run on the R9700 today

One line each: best feature + the single blocker. Almost always "no AMD/ROCm backend" or
"gfx1201 broken." (From `amd-rdna4-inference-engines-research.md`.)

| Engine | Lang | Best feature | Single blocker | Watch? |
|---|---|---|---|---|
| **candle-vllm** (guoqingbao) | Rust | **Full GDN impl** + continuous batching + MambaCache + fp8/TurboQuant; README 27B Q4/FP8 ~49 tk/s | **No AMD backend** (CUDA/Metal only; candle ROCm PR #3424 = RDNA3-only, unmerged) | **YES** (watching) |
| **mistral.rs** | Rust | Shipping GDN (v0.8.0) + PagedAttn + block-level prefix cache + fp8 KV | **No ROCm backend** (open request #1345/#431, no branch) | **YES** (watching) |
| **Atlas** (Avarok) | Rust | GDN + radix-tree cache + 6-tier KV + SLAi scheduler | **NVIDIA-only** (GB10/SM121; AMD = "future") | **YES** (watching) |
| **pegainfer** (xiaguan) | Rust | GDN + batching + prefix cache | **CUDA-only** (cuBLAS/FlashInfer/NCCL) | **YES** (watching) |
| **Modular MAX** | Mojo | **Most mature GDN kernel** (`gated_delta.mojo`, dedicated arch) + ragged batching | **gfx1201 broken** (flash-attn gfx12 WMMA intrinsic fails) **+ no Qwen3.6 reg + no quant-to-fit (bf16→54 GB) + prefix cache hardcoded off** | **YES** (watching) |
| **vllm-rs** (guoqingbao) | Rust | Batching + prefix cache + TurboQuant | No GDN (dense only) + CUDA/Metal | — |
| **TGI** | Rust | Continuous batching + prefix cache (core strength) | No GDN evidence; ROCm = CDNA-focused, RDNA4 unproven | — |
| **MLC-LLM / TVM** | C++/TVM | Arch merged in core (#3449) + batching + RadixAttn + KV quant in general | **No shipping RDNA4 serve path** — build TVM+MLC+model from source; hybrid-on-RDNA4 unproven | Long-shot only |
| **Modular MAX → ONNX-RT GenAI / AMD Lemonade** | C++ | AMD-first ONNX path | ONNX op set is **softmax-only** — GDN needs 5 new ops (proposal #7689 only); Lemonade falls back to llama.cpp | — |
| **Crane / Atoma / rvLLM / Shimmy / Ratchet / Luminal / ZML / SHARK / burn-CubeCL** | Rust/Zig/MLIR | various (batching, int4, WebGPU AMD) | **No GDN** (and/or no RDNA4 path) | — |
| **Cloudflare Infire** | Rust | Batching + session-affinity cache | **Closed-source/hosted, not self-hostable** | — |

> Tanguille is **already watching on GitHub**: hipfire, candle-vllm, mistral.rs, atlas,
> pegainfer, modular (MAX). The pattern across the whole table: **three Rust engines have
> the complete capability set** (candle-vllm, mistral.rs, Atlas) **— and all three are
> unusable on the R9700 purely for lack of an AMD GPU backend.** The capabilities exist;
> they just aren't assembled in one engine on this GPU yet.

---

## 4. Cross-cutting insight — prefix caching on the GDN hybrid is broken almost everywhere

The single hardest requirement is **prefix/prompt caching on the GatedDeltaNet hybrid** —
the GDN recurrent state breaks the assumptions prefix caches are built on, so engines
implement it *last* (if at all). It is disabled or broken in nearly every engine:

- **sglang (ours, ROCm):** RadixAttention *works* — but radix-vs-overlap are mutually
  exclusive (the `extra_buffer` mamba strategy asserts `is_cuda()/is_musa()/is_npu()`,
  excluding ROCm; radix-on force-sets `disable_overlap_schedule=True`). → **cache XOR batch.**
- **llama.cpp:** `cache_reuse` unsupported for hybrid → full re-prefill (#21383); plus an
  illegal-memory-access crash on prompt-cache save under agentic load (#21383, #19518).
- **Modular MAX:** `enable_prefix_caching: False  # TODO: Remove when Deltanet supports
  prefix caching` — hardcoded off.
- **candle-vllm** *claims* it works (via MambaCache) — but CUDA-only, so **unverifiable here.**

**The one engine that demonstrably does caching + batching TOGETHER on this GPU is vLLM**
(APC + continuous batching are not mutually exclusive in vLLM; measured 6.3× cached TTFT
*while* batching to 47 tok/s @C8). That structural property is vLLM's defining advantage
on RDNA4 and the reason it's the standby even though it loses single-stream.

---

## 5. Decision matrix — "if you prioritize X → choose Y"

| If you prioritize… | Choose | Why (measured) |
|---|---|---|
| **(a) Raw single-user speed** | **sglang fork (Config A)** today; *re-evaluate vs llama.cpp/Hipfire after pending tests* | 14.96 tok/s — 2× vLLM's per-stream (8). The pending engines (~42–48 / ~44 tok/s) *could* beat it, but only sglang also has a working cache today. |
| **(b) Agentic caching + a few concurrent sessions** (the user's profile) | **sglang Config A** if concurrency stays ~1–2 (3.5× cached TTFT, full speed); **vLLM** the moment a few sessions overlap (6.3× cached TTFT **+** batches without collapse) | sglang's cache only survives at low concurrency (batching kills it); vLLM is the only one that keeps the cache hot under concurrent load. |
| **(c) Max throughput under load** | **vLLM** | 47 tok/s aggregate @C8 *with APC on* — beats sglang's best-ever 28.8 (Config B, cache off). TTFT 1.4–3.3 s vs sglang's 80 s collapse. |
| **(d) Future-proofing / Rust-native** | **Watch** candle-vllm / mistral.rs / Atlas (full feature set, awaiting AMD backend) and **Hipfire** (gfx1201-native, awaiting batching) | Complete capability set exists in Rust today but none has both GDN+RDNA4+batching+cache assembled. Hipfire is closest on hardware; the others closest on features. |

**The crux:** the sglang-vs-vLLM decision hinges on **latency vs both-at-once**.
sglang = **15 tok/s single-stream but batching XOR caching**. vLLM = **47 tok/s aggregate +
6.3× cache that coexists with batching, but only 8 tok/s single-stream**. Given the user's
"few sessions + speed-priority + wants caching," **sglang Config A is the interim pick, vLLM
the documented standby** for when concurrency rises.

---

## 6. STILL PENDING — what would change the verdict

Two on-hardware tests are outstanding; either could move the recommendation:

1. **llama.cpp decode-speed measurement (on this rig).** Expected ~42–48 tok/s single-stream
   — if real, that is **~3× sglang's 15 tok/s**, which would be decisive *for the user's
   speed priority*. **But** the verdict only flips if single-stream speed outweighs the known
   disqualifiers: **prefix cache broken on the hybrid (#21383)**, **batching crash on ROCm
   (#19518)**, and an **agentic-load illegal-memory crash (#21383)**. Since the user explicitly
   wants caching, even a fast llama.cpp likely lands as "fastest-but-no-cache single-user
   option," not an outright winner. *Measure decode tok/s; confirm whether the cache/crash
   bugs reproduce on our build.*

2. **Hipfire on-hardware test (on this rig).** Expected ~44 tok/s (author number, 7900 XTX),
   gfx1201-native. If it holds on the R9700, Hipfire becomes a strong **single-user-only**
   contender. **But** it has **no continuous batching** (serial), only a weak model-specific
   cache, and is **alpha / single-author / AI-built / no GPU CI / proprietary `.mq4`** — so it
   could win raw decode yet still lose on the user's caching requirement and on production
   risk. *Measure decode tok/s on the R9700; sanity-check that "no batching" and the
   not-a-real-cache caveats hold.*

**Until both land**, the interim verdict stands: **sglang fork (Config A) as primary, vLLM as
the both-at-once standby.** A confirmed fast-AND-non-crashing llama.cpp, or a fast Hipfire
whose caching turned out to be real, would be the two findings most likely to revise it.

---

## Sources

- [`sglang-rdna4-benchmarks.md`](./sglang-rdna4-benchmarks.md) — measured sglang Config A/B,
  KV-dtype A/B, and vLLM-on-this-rig batching + APC numbers (all numbers in Sections 1–2, 4–5).
- [`amd-rdna4-inference-engines-research.md`](./amd-rdna4-inference-engines-research.md) —
  engine-by-engine arch/AMD-support research, GitHub issue citations, the cross-cutting
  prefix-cache finding (Sections 2 PENDING rows, 3, 4).

> Status: untracked interim decision note, nothing committed. Holds open pending the
> llama.cpp and Hipfire on-hardware measurements.
