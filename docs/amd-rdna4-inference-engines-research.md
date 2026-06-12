# Non-Python inference engines for Qwen3.6-27B on AMD RDNA4 (R9700/gfx1201) — research

Companion to `sglang-rdna4-benchmarks.md`. Question researched: *which systems-language
(C++/Rust/Zig/Mojo/MLIR) inference engine can serve **Qwen3.6-27B-AWQ** on a single
**R9700 (gfx1201, RDNA4, 32 GB, ROCm 7.x)** with continuous batching to ~10 sessions, a
full-131K-context session, strong prefix caching, and KV-cache compression?*

> Method: deep-research harness — 5 search angles, 25 sources fetched, 115 claims extracted,
> 25 adversarially verified (3-vote, need 2/3 to confirm). 17 confirmed, 8 killed. The final
> automated synthesis was cut off by a provider session limit; this document is the
> hand-synthesis from the **verified** claims. Sources are primary (GitHub issues/PRs/docs)
> unless noted. Verification status flagged per claim.

## The gating fact

**Qwen3.6 is a Qwen3-Next-family hybrid: 64 layers = 16 full-attention + 48 Gated DeltaNet
(GDN) linear-attention layers** (`full_attention_interval=4`). GatedDeltaNet is a brand-new
non-softmax token mixer. Engine support for it is the filter that eliminates almost everything
*before* batching/caching/KV-quant even matter — and crucially, the features we care most about
(prefix cache, continuous batching) are exactly the ones that engines implement **last** for a
hybrid, because the GDN recurrent state breaks the assumptions those features are built on.

## Per-engine verdict (ranked by how close to usable today)

| Rank | Engine | Lang | Runs GDN hybrid on RDNA4? | Prefix cache (hybrid) | Batching→10 | KV compression | Single biggest blocker |
|---|---|---|---|---|---|---|---|
| 1 | **llama.cpp** | C++ | **Yes** — merged, runs on ROCm | **No** (`cache_reuse` unsupported for hybrid) | **Broken** (kernel error on 2nd batched req) | KV q8/q4 (generic) | Hybrid prefix-cache unsupported **+ crashes** under agentic load; correctness-only perf |
| 2 | **MLC-LLM / TVM** | C++/TVM | Arch merged in core (`mlc-llm#3449`); **no shipping RDNA4 serve path** | unproven for hybrid | unproven | int3/int4 weight, KV quant | Must build TVM+MLC+model from source; RDNA4 serving unproven |
| 3 | **AMD Lemonade SDK** | C++/Py wrap | Only via its bundled **llama.cpp-rocm** backend (= row 1's limits) | No (inherits llama.cpp) | Broken (inherits) | inherits | Native ORT-GenAI path has **zero** GDN operators (see ONNX) |
| 4 | **mistral.rs** | Rust | **No** (Qwen3-Next is an open request, `#2125`) | n/a | n/a | yes (ISQ, KV) | No GatedDeltaNet support; candle ROCm/RDNA4 path weak |
| 5 | **TGI** | Rust | No evidence of Qwen3-Next | n/a | yes (its core feature) | fp8/etc | No GDN support; ROCm is CDNA-focused, RDNA4 unproven |
| 6 | **nod-ai / SHARK** | MLIR | No GDN | n/a | partial | — | No GatedDeltaNet model; serving maturity low |
| 7 | **ZML** | Zig/MLIR | No GDN model | n/a | — | — | No Qwen3-Next implementation |
| 8 | **ONNX Runtime GenAI** | C++ | **No** — ONNX has no linear-attention op | n/a | yes | yes | ONNX op set is softmax-only; GDN needs 5 new ops (proposal only) |
| – | **burn/CubeCL, Ratchet, llamafile, PowerInfer, Nexa** | Rust/C++ | No GDN | — | — | — | No GatedDeltaNet; research-grade for 27B serving |

## Update — the newer Rust wave + Mojo (the blocker *flipped*)

Follow-up research (12 engines, mid-2026) changes the picture in one important way: **the
GatedDeltaNet architecture IS now implemented in a wave of newer Rust engines** — so the gating
filter is no longer "nobody supports the arch." It's now **"the engines that support the arch
don't support your GPU."** Almost every Rust engine that implements GDN is CUDA/Metal-only.

| Engine | Lang | GDN hybrid (Qwen3.5/3.6) | AMD RDNA4 gfx1201 path | Batching | Prefix cache | KV quant | Biggest blocker |
|---|---|---|---|---|---|---|---|
| **candle-vllm** (guoqingbao) | Rust | **✅ full impl** (`deltanet.rs`, `qwen3_5.rs`); README: 27B BF16 36 tk/s, Q4/FP8 ~49 tk/s | ❌ CUDA/Metal only (no `rocm` feature; candle ROCm PR #3424 = RDNA3 only, unmerged) | ✅ continuous | ✅ + MambaCache | ✅ fp8 + TurboQuant (8/4/3-bit) | **No AMD backend** |
| **mistral.rs** | Rust | **✅ shipping v0.8.0** (PR #1864 Qwen3-Next, #1993 Qwen3.5); GGUF path still buggy (#2125) | ❌ ROCm = open request only (#1345/#431), maintainer "interested", no branch | ✅ PagedAttn | ✅ block-level (#1890) | ✅ fp8 (`--pa-cache-type f8e4m3`) | **No ROCm backend** |
| **Atlas** (Avarok) | Rust | ✅ GDN + Qwen3.5/3.6/Next | ❌ NVIDIA GB10/SM121 only; AMD = "future" | ✅ + SLAi sched | ✅ radix-tree | ✅ 6-tier | **NVIDIA-only** |
| **pegainfer** (xiaguan) | Rust | ✅ GDN (Qwen3.5-4B) | ❌ cuBLAS/FlashInfer/NCCL — CUDA only | ✅ | ✅ | — | **CUDA-only** |
| **Modular MAX** | **Mojo** | **✅ Qwen3.5** (`gated_delta.mojo` kernel, dedicated arch) — **but Qwen3.6 not registered** | ⚠️ **broken on gfx1201** — flash-attn gfx12 WMMA intrinsic fails (Modular forum); CDNA MI300X only in practice | ✅ ragged | ❌ **disabled for GDN** (`enable_prefix_caching: False # TODO`) | fp8 KV exists but **not for Qwen3.5 arch** (bf16/fp32 only → 54 GB, won't fit 32 GB) | **gfx1201 flash-attn broken + no Qwen3.6 + no quant-to-fit** |
| **Hipfire** (Kaden-Schutt) | Rust | **✅ Qwen3.6-27B** (`hipfire-arch-qwen35`; HF `schuttdev/hipfire-qwen3.6-27b`) | **✅ gfx1201-NATIVE** (WMMA kernels, author runs 4× R9700) | ❌ **none** (serial) | ⚠️ model-specific, not a general system | ✅ asym3 KV + q8 DeltaNet state | **No continuous batching + alpha/unproven** |
| **vllm-rs** (guoqingbao) | Rust | ❌ dense Qwen only | ❌ CUDA/Metal | ✅ | ✅ | ✅ TurboQuant | No GDN + no AMD |
| **Crane / Atoma / rvLLM** | Rust | ❌ (dense / Llama / Gemma only) | ❌ CUDA(+Metal) | ✅ | varies | varies | No GDN + no AMD |
| **Shimmy / Ratchet / wgml / wgpu-llm** | Rust | ❌ no GDN | ⚠️ AMD via Vulkan/WebGPU | ❌/partial | — | int4 (Shimmy) | No hybrid arch (WebGPU stacks lack GDN) |
| **Luminal** | Rust | ❌ no GDN | ⚠️ ROCm PR #336 = RDNA3 only, unmerged | example-level | ❌ | ❌ | No GDN + RDNA4 not targeted |
| **lm.rs / rustformers-llm / Kalosm / Paddler / mlxcel** | Rust | ❌ | CPU / Apple / proxy | — | — | — | Not a GPU server for this / unmaintained |
| **Cloudflare Infire** | Rust | ❌ | ❌ NVIDIA Hopper, **closed-source/hosted** | ✅ | ✅ session-affinity | ❌ | Not self-hostable at all |

**Three Rust engines have *complete* GDN + batching + prefix cache + KV quant** — candle-vllm,
mistral.rs, Atlas — **and all three are unusable on the R9700 because they have no AMD GPU
backend.** The single engine that *is* gfx1201-native (Hipfire) is the one missing continuous
batching and a real prefix cache. The capability set you need exists; it just isn't assembled in
one engine on this GPU yet.

### Mojo / Modular MAX detail
MAX has the most *mature* GDN implementation found (real `gated_delta.mojo` GPU kernel, dedicated
`Qwen3_5ForConditionalGeneration` arch) and Modular is actively bringing up RDNA — yet it can't
serve this model on gfx1201: (1) the flash-attention kernel for the 16 full-attn layers hits a
**gfx12 WMMA intrinsic incompatibility** (Modular's own forum), (2) **Qwen3.6 isn't registered**
(only Qwen3.5-27B), (3) the Qwen3.5 arch supports **only bf16/fp32** → 54 GB, won't fit 32 GB, and
(4) **prefix caching is hardcoded off for GDN** (`enable_prefix_caching: False # TODO: Remove when
Deltanet supports prefix caching`). Free for 1 GPU under the Modular Community License. CDNA-only
in practice.

### Hipfire detail (the one gfx1201-native option — adversarially verified)
Real codebase (21 Rust crates, 431★/46 forks, real external issue reporters, MIT/Apache, dedicated
`hipfire-arch-qwen35`, published `schuttdev/hipfire-qwen3.6-27b` `.mq4` checkpoint). Author owns a
4× gfx1201/R9700 validation box; gfx1201 WMMA kernels + q8 error-feedback DeltaNet-state KV quant
are real. **Caveats:** (a) **no continuous batching** anywhere in the codebase (HIGH confidence) —
concurrent sessions serialize; (b) prefix caching is mentioned once, model-specific, **not a
general system** (the first scout overstated it); (c) **alpha**, single-author, **AI-built** (its
`CLAUDE.md` names Claude Opus as primary engineer), **no GPU CI**, all RDNA4 validation
self-reported (no independent benchmark), proprietary `.mq4/.hf4` formats. Decode ~44 tok/s on a
7900 XTX (author numbers). **Verdict: promising-but-unproven alpha** — fine for single-user /
sequential decode, not for production agentic serving (no batching). The thing to watch.

### Cross-cutting finding (the deepest one)
**Prefix caching on the GDN hybrid is disabled or unsupported in nearly every engine** — llama.cpp
(`cache_reuse` unsupported + a prompt-cache-save crash, #21383), Modular MAX (`enable_prefix_caching:
False`, TODO), and your own SGLang on ROCm (radix-vs-overlap mutually exclusive). The GDN recurrent
state is fundamentally hard to prefix-cache; it is the single hardest of the requirements,
everywhere. candle-vllm claims it works (via `MambaCache`) — but CUDA-only, so unverifiable here.

## Evidence by engine

### 1. llama.cpp (C++, ROCm/HIP + Vulkan) — the only systems engine that *runs* the arch today

- **GatedDeltaNet/Qwen3-Next support is MERGED** — PR `ggml-org/llama.cpp#16095`, merged
  2025‑11‑28, closing feature request `#15940`. *[confirmed 3-0]*
- **Explicitly correctness-only:** *"this implementation will be focused on CORRECTNESS ONLY.
  Speed tuning … will come in future PRs."* So it is functional but **not** performance-tuned.
  *[confirmed 3-0]*
- **RDNA4 gfx1201/R9700 ROCm path works in general** — discussion `#15021` shows R9700 on
  ROCm 7.1.1 doing `pp512 ≈ 5025 t/s` with flash-attn. *[confirmed 3-0]*
- **It does load and run the Qwen3.5-27B hybrid** (64 layers, hybrid attn + Mamba2/GDN,
  `full_attention_interval=4`); offloads 49/49 layers to GPU and responds. *[confirmed 3-0,
  issues #21383 / #19518]*
- **DISQUALIFYING for this use case:**
  - **Prefix/prompt cache does not work on the hybrid:** *"cache_reuse is not supported by this
    context"* → *"forcing full prompt re-processing due to lack of cache data."* *[confirmed
    3-0, #21383]* This fails requirement #4 outright — the agentic reuse win is gone.
  - **Crash under the exact agentic pattern we run:** illegal-memory-access during prompt-cache
    save/update after the response is sent, with ~29K-token rapidly-changing prefixes through
    tool-call loops, single-GPU. *[confirmed 3-0, #21383]*
  - **Batching is broken on ROCm for the hybrid:** first request succeeds, a second request
    (6,463 tokens) triggers a kernel error during batch processing. *[confirmed 3-0, #19518]*
  - Poor GPU utilization (60–70%, CPU ~102%) on ROCm for the hybrid. *[weak, 1-1, #18351]*
- **Net:** llama.cpp clears the architecture bar but fails **prefix-cache (hybrid)**,
  **batching (ROCm hybrid)**, and is **not perf-tuned** — i.e. it loses on every requirement
  that made us pick SGLang, *and* adds an agentic-load crash. A 5x-slower-than-Qwen3-30B claim
  was **refuted (0-3)**, so don't repeat that specific number — but the qualitative "not tuned"
  stands.

### 2. MLC-LLM / TVM Unity (C++/TVM, ROCm + Vulkan)

- **Arch supported in the core:** Qwen3.5 GatedDeltaNet landed upstream via `mlc-ai/mlc-llm#3449`,
  and the hybrid needed a **KV-cache refactor** (different/hybrid KV settings) — confirming it is
  a non-trivial engine change, not a drop-in. *[confirmed 2-1 / 3-0, web-llm#778]*
- **No shipping turnkey path:** the downstream WebLLM still didn't ship Qwen3.5 as of Mar–Apr
  2026; maintainer says *"build TVM and MLC-LLM from source and compile this model for the …
  target."* *[confirmed 3-0]* No RDNA4/gfx1201 serving benchmark exists.
- MLC's serve engine *does* have continuous batching + RadixAttention-style prefix cache + KV
  quant **in general** — but none of that is demonstrated for this hybrid on RDNA4.
- **Blocker:** build-everything-from-source; RDNA4 serving of the hybrid is unproven. Highest-
  upside long shot, but it is a project, not a deployment.

### 3. AMD Lemonade SDK + ONNX Runtime GenAI

- Lemonade ships a **`llamacpp-rocm`** backend targeting `gfx120X` (RDNA4) — so for *this* model
  Lemonade is **just llama.cpp underneath** and inherits every row-1 limitation (no hybrid prefix
  cache, batching crash). It does not add a better path for the hybrid.
- Lemonade's *native* high-performance path is **ONNX Runtime GenAI**, and **ONNX cannot
  represent GatedDeltaNet:** every ONNX/ORT attention operator is softmax-based; supporting GDN
  needs 5 brand-new ops (`LinearAttentionRecurrent`, `LinearAttentionChunk`, `CausalConv1D`,
  `GatedRMSNorm`, `L2Normalize`) that are **only a proposal** (`onnx/onnx#7689`,
  justinchuby gist). *[verification cut off by session limit — primary-source-reported, not
  independently 2/3-confirmed; treat as strong but unverified]*
- **Blocker:** the AMD-first ONNX route is a dead end for this architecture for now.

### 4–8. The rest (no architecture support today)

- **mistral.rs (Rust/candle):** Qwen3-Next is an **open request** (`#2125`), not shipped; candle's
  ROCm/RDNA4 support is weak. Blocker: no GDN + no real RDNA4 ROCm path.
- **TGI (Rust):** strong continuous batching + prefix caching in general, but no evidence of
  Qwen3-Next support and its ROCm story targets CDNA (MI-series), not RDNA4. Blocker: no GDN.
- **nod-ai/SHARK (MLIR, AMD-first), ZML (Zig/MLIR):** no GatedDeltaNet model implementation;
  serving maturity for a 27B hybrid is not there. Blocker: no GDN.
- **burn/CubeCL, Ratchet (WebGPU), llamafile, PowerInfer, Nexa SDK:** none implement GDN; not
  contenders for 27B hybrid serving. (llamafile = llama.cpp build → same arch status, worse
  server.)

## Verdict

**No non-Python systems-language engine currently beats the SGLang fork for this model on
RDNA4.** The reason is structural, not incidental:

- **llama.cpp** is the *only* C++/Rust/MLIR engine that runs the GatedDeltaNet hybrid on ROCm
  today — but for the hybrid it has **no working prefix cache**, **broken batching on ROCm**, a
  **crash under agentic tool-call load**, and is **explicitly not perf-tuned**. That is strictly
  worse than the current SGLang setup on every requirement we care about. (Our SGLang gets ~15
  tok/s single-stream, fp8 KV with native gfx1201 kernels, *working* RadixAttention; its only
  limitation is radix-vs-overlap exclusivity — a tuning tradeoff, not a missing/ crashing feature.)
- **MLC-LLM** is the only credible future challenger (real continuous batching + prefix cache +
  KV quant, arch merged in core), but needs everything built from source and has **no proven
  RDNA4 serving path** — a research project, not a swap-in.
- Every other engine **does not support the architecture at all** on this hardware.

**Recommendation:** stay on the SGLang fork. The features that made SGLang the right call
(RadixAttention prefix cache + fp8 KV + a working gfx1201 path) are precisely the ones the
non-Python engines either crash on or haven't built yet for a GatedDeltaNet hybrid. Re-evaluate
**MLC-LLM** and **llama.cpp's hybrid prefix-cache** in ~1–2 quarters: the single highest-leverage
thing to watch upstream is llama.cpp closing `#21383` (hybrid `cache_reuse` + the prompt-cache
crash), which would make it a genuine option.

## Sources (primary unless noted)

- llama.cpp Qwen3-Next merge: `ggml-org/llama.cpp#16095`, feature req `#15940`
- llama.cpp hybrid prefix-cache + crash: `#21383`; batching kernel error `#19518`; ROCm hybrid
  GPU-util `#18351`; GGUF convert `#17822`
- llama.cpp RDNA4/R9700 ROCm perf: discussion `#15021`; Lemonade ROCm builds: `lemonade-sdk/llamacpp-rocm`
- MLC/WebLLM hybrid: `mlc-ai/web-llm#778`, core PR `mlc-ai/mlc-llm#3449`
- ONNX no-linear-attention: `onnx/onnx#7689`, justinchuby gist (verification incomplete)
- mistral.rs Qwen3-Next request: `EricLBuehler/mistral.rs#2125`
- AMD-first stacks: `nod-ai/shark-ai`, `zml/zml`, `lemonade-sdk/lemonade`
- SGLang RDNA4 reference: `mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference`

> Status: untracked research note, nothing committed. Confidence is high on llama.cpp (many
> 3-0 primary-source confirmations) and on the overall "stay on SGLang" verdict; medium on the
> ONNX/MLC specifics (some verification cut off by the provider session limit).
