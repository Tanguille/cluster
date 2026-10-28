# Speculative decoding on qwen38-27b-vllm (gfx1201): evaluated and rejected

Standing decision, not a point-in-time log — `qwen38-27b-vllm.yaml` points here
by name. Evaluated 2026-09-07; revisit only on the upstream fixes named below.

Evaluated DSpark end to end on the live deployment, plus a nightly-image
regression retest that ran alongside it. Every number here was measured on
control-1 against the production pod on 2026-09-07; nothing is carried over from
published benchmarks.

## Verdict

**DO NOT ENABLE speculative decoding of any kind on this deployment.** Re-open
only when one of the named upstream issues is fixed, not on a new idea.
The Hermes cron job `spec-decode-watch` (profile `local-llm-operator`, Mondays 09:00, script on the
Hermes PVC) posts to Discord when one of the open blockers changes state.

Status is not uniform across methods — two were measured on this hardware, two
were ruled out on mechanism without a benchmark:

| method | status | basis | why |
| --- | --- | --- | --- |
| **n-gram / prompt-lookup** | **corrupts output** | measured, incl. production | vllm#39273: SSM state corruption on hybrid GDN models — truncated/repeated fragments. Faster (see caveat below) and silently wrong. |
| **DSpark** | rejected | measured | 0.59x (18.84 vs 31.73). Drafter KV is additive and bf16: +70% B/token, forcing the cap to 88K, below the 112K production peak. |
| **MTP** | rejected | measured 2026-10-24 | Corrupts output on nightly `83c078d` (k=1 and k=3), see "MTP retest 2026-10-24". Earlier: -27% at M=5; 09-12 retest: 48K conc 2-5 loses (33/40/44/39 vs 45/51/62/71) and greedy output diverges. |
| **DFlash2** | blocked | measured 2026-10-27 | Cannot boot beside the UltraQuant KV (page padding, vllm#62558); on an fp8-KV stack +40% decode at 4K but pool 143K and a 120K cap. See "DFlash2 test 2026-10-27". |
| **EAGLE3** | blocked | **not benchmarked** — mechanism only | Same drafter-KV tax as DSpark. No standalone EAGLE3 drafter is published for Qwen3.8-27B. |

Also untested, and worth knowing before any retry: n-gram was never run against
a same-prompt spec-off control, and no method was tested under sustained
concurrency beyond conc 5.

Two independent blockers apply to every **drafter-based** method (MTP, DSpark,
EAGLE3, DFlash), either one disqualifying:

1. **Drafter KV is additive and bf16** — pushes the model-length cap below the
   112K production peak.
2. **vllm#41640 (closed unmerged 2026-09-08)** — vLLM cannot identify a
   GQA drafter's KV group on a non-MLA target, so every group including the
   Mamba groups is treated as a draft group. That disables prefix caching and
   makes the KV offload tier write-only. This deployment cannot afford either:
   removing the fs tier alone halved single-stream decode. Fixed for MTP only
   by vllm#55390 (merged 2026-09-22), unverified here.

A third, the tool-calling/structured-output wedge (vllm#49002), was fixed by
vllm#55223 and verified here 2026-09-12.

And the one blocker that kills the **drafter-free** method: **vllm#39273**, the
GDN corruption bug. n-gram sidesteps both of the above by construction —
no draft model, no draft KV group — and is still unusable because it produces
wrong output on this model family.

So the exit condition is narrow: **vllm#39273 fixed** re-opens n-gram, which is
the only method whose performance was ever worth having here. Everything
drafter-based additionally needs a draft-group fix (merged for MTP only, vllm#55390) and a drafter under ~2.5 GiB that does not currently exist.

n-gram is fast — measurably 1.8-2.1x on echo-heavy work — and it silently
corrupts output.

**Disqualifying: vllm#39273, "Ngram speculative decoding produces corrupted
output on hybrid GDN (Qwen3.5) models."** Reported symptom is repeated and
truncated fragments that degrade progressively; the diagnosis is SSM state
corruption, not a sampling error. Qwen3.8-27B is a GDN hybrid, so this is our
exact configuration. Found the hard way: after a live production trial, Hermes
reported truncated outputs. Related, for the tool-calling path specifically:
vllm#10442 and vllm#9423 (spec decode truncating under guided/structured
decoding).

Nothing in the engine's metrics flags this. Acceptance, tokens/step, preemptions
and queue depth all looked healthy throughout the corrupting run — 43-67%
acceptance and 2.7-3.7 tokens/step on live traffic. The counters measure whether
drafts were *accepted*, never whether the result was *correct*.

**Methodology lesson, the important one in this document:** the tooling to catch
this existed and its result was not treated as a gate. `spectest.py` compares
generated text against the source and reports `OK` / `TRUNCATED` / `DIVERGED`;
it ran, it reported **`DIVERGED`**, and that was dismissed as a formatting
artifact after eyeballing only the first 200 characters — while the known
corruption mode is *progressive*, so the tail was where the evidence would have
been. Every other script measured speed alone. No performance number was ever
gated on a passing quality check, so a correctness regression reached production
looking like a 2x win.

Any future speculative-decoding trial must treat a `spectest.py` pass as a
precondition for quoting a performance number at all, and must read the whole
output, not the head.

The performance findings below remain valid and are kept for when the upstream
bug is fixed; they are not a reason to enable it before then.

### Performance, for the record only

**n-gram is a large win on echo-heavy traffic and a loss on traffic with nothing
to quote back.** Same pod, same config, varying only the prompt: **61.64 tok/s**
on a real "edit this manifest" prompt, and **21.4** on a synthetic prompt with
little to look up.

> **The speedup ratios in this document are PROVISIONAL.** Every "vs baseline"
> figure compares against 31.73 tok/s, which was measured on a *different
> prompt* (synthetic, 4K) and before the config change. Decode rate should be
> largely prompt-independent, and a post-revert spec-off run on the same pod
> measured 32.80 tok/s / 30.4 ms/step, which is consistent — but the
> same-prompt control was never run, because the corruption was found first and
> the config reverted. Treat 1.8-2.1x as indicative, not settled. Running that
> control is the first step of any future retry.

Drafter-based methods (MTP, DSpark, EAGLE3, DFlash) remain dead here, for three
independent reasons below, any one disqualifying. DSpark measured 18.84 tok/s
(0.59x). n-gram clears all three — no draft model, no draft KV group, no cap
change.

### The model that fits every measurement

Enabling n-gram costs a **flat ~50 ms/step against the baseline's ~32 ms**, a
~1.56x tax paid on *every* step, including steps where no draft is proposed
(CPU-side suffix search, the forced V1 model-runner fallback, and async
scheduling being disabled). Acceptance decides whether that is earned back:

| workload | accept% | tokens/step | tok/s | vs baseline |
| --- | --- | --- | --- | --- |
| synthetic, poor lookups | 30.8 | 1.03 | 21.4 | 0.67x |
| synthetic, lucky lookups | 95-97 | 2.06-2.18 | 41-43 | 1.3x |
| real manifest edit (8K) | 89.7 | 3.33 | 61.6 | **1.94x** |

A sweep over five workload shapes built from real repo content confirms it,
ordered by how much of the output already exists in the prompt (2 reps each,
`walltime.py` with `PROMPT_FILE`, 800 tokens generated):

| shape | tok/s | vs 31.73 | accept% | tokens/step | ms/step |
| --- | --- | --- | --- | --- | --- |
| refactor-python (emit modified script) | 65.84 | 2.07x | 76-93 | 3.36 | 49-52 |
| edit-manifest (emit modified yaml) | 57.24 | 1.80x | 82-85 | 3.10 | 53-55 |
| extract-json (quote values into JSON) | 38.65 | 1.22x | 69-81 | 2.06 | 52-54 |
| explain-code (prose, quotes lines) | 30.87 | 0.97x | 48-75 | 1.58 | 49-55 |
| summarize-doc (prose, own words) | 23.32 | 0.73x | 28 | 1.16 | 50 |

Perfectly monotonic in echo ratio, and `ms/step` held at 48.2-55.2 across every
row of both tables while tok/s moved 3x.

**Break-even is ~1.56 tokens/step.** Production traffic on this model is
coding-agent and tool-calling work whose output heavily quotes its input, i.e.
the top of that table. Live production traffic measured 43-67% acceptance and
2.7-3.7 tokens/step before the run was reverted for corruption.

Corrected from an earlier draft of this document: a first pass concluded n-gram
was a net loss and blamed the LDS gate at M=2 for a ~4.3x step cost. That figure
came from subtracting a *guessed* prefill from `spectest.py`'s
prefill-inclusive rate. Direct measurement puts the step cost at 1.56x, not
4.3x, and the earlier "floor" prompt turned out to be bimodal noise rather than
a floor. The LDS gate is real and does make a multi-token step more expensive —
it is simply much cheaper than the token yield it buys.

## Method note: the ITL counter lies under speculative decoding

`bench/longctx.py` derives tok/s from `vllm:inter_token_latency_seconds`
sum/count. Under speculative decoding that counter records far fewer events than
tokens emitted — 184-426 events for a fixed 511-token generation — so its mean
is closer to per-*step* than per-token and the derived rate is wrong.

It reported DSpark at 7.37 tok/s. Wall-clock measurement of the same config on
the same pod gave 18.84. **Use `bench/walltime.py` (tokens ÷ wall clock after
first token) for anything with speculative decoding on.** longctx.py remains
correct for non-speculative runs, where one token is one ITL sample.

## DSpark measurement

Drafter `RadixArk/Qwen3.8-27B-DSpark` @`b9a5dbdf03bc999c6c73c426b19c2d9041cea393`
(bf16, 3.46 GiB, 5 layers, `block_size` 7), `num_speculative_tokens` 7,
`draft_sample_method` probabilistic, adaptive verification off.

| config | tok/s | TTFT | how measured |
| --- | --- | --- | --- |
| pinned baseline, no spec decode | 31.73 | 4.07s | longctx, ITL-derived (valid, non-spec) |
| DSpark, cap 88,000 | 18.84 | 4.28s | walltime, wall-clock |

0.59x — a 1.68x slowdown. TTFT is unchanged, so this is entirely a decode-side
cost. Both runs: 4K prompt, 4 clean reps, idle preflight, unique salt.

### Why it loses, from the engine's own counters

```text
num_drafts_total          1992
num_draft_tokens_total   13659     (6.86/draft, i.e. the full block of 7)
num_accepted_tokens_total 2574     → 18.8% acceptance
accepted per position:  0:1281  1:526  2:265  3:184  4:140  5:103  6:75
```

Per-position acceptance decays 64% → 26% → 13% → 9% → 7% → 5% → 3.8%. Mean
accepted is 1.29 tokens/step, so a step emits ~2.29 tokens instead of 1. That is
a real 2.3x token yield, and it still loses, because the step got more expensive
than that.

Decode here is memory-bandwidth-bound (every GEMM is already at the memory
ceiling — see the standalone-bench findings). DSpark runs the drafter **7 times
sequentially** per step: 7 x 3.46 GiB ≈ 24 GiB of weight traffic against the
W4A16 target's single ~15 GiB pass. You buy 2.29x tokens for ~2.6x the traffic.

### Break-even ceiling for a drafter on this box

At the measured acceptance curve, a step must satisfy
`target + 7·drafter ≤ 2.29 · target`, i.e. **`drafter ≤ 0.184 · target`**.
Against a ~13.5 GiB effective W4A16 target that is **~2.5 GiB**. The only
published Qwen3.8-27B DSpark drafter is 3.46 GiB ≈ 0.256x — roughly 40% over
budget on weight traffic alone, before the KV tax below. This is why the
measured 0.59x is worse than the ~1.0x the traffic math alone predicts.

A W4A16 drafter would cut draft weight traffic ~4x and clear that ceiling, but
it does not fix either problem below, so it is not on its own a reason to retry.

## The three blockers

### 1. Drafter KV is additive, and it is bf16

The drafter brings its own KV cache: 5 full-attention layers at bf16 while the
target's KV is fp8_e4m3. Per-token KV goes **33,494 B → 56,940 B (+70%)**
(engine's own figure: 7.95 GiB needed for 150,000 tokens).

Consequences, in order of how they bit:

- The first boot at `maxModelLen` 150,000 **refused to start**:
  `ValueError: ... 7.95 GiB KV cache is needed, which is larger than the
  available KV cache memory (5.49 GiB) ... estimated maximum model length is
  98560`.
- At the retry cap of 88,000 the pool came out 96,724 tokens = **1.10x**
  pool/cap, inside the band this config measures as slow-to-unstable (the
  bisect puts the cliff at ~1.17x). The honest cap for that pool is ~82,000.
- 88,000 is already **below the 112K observed production peak**, so shipping it
  would fail prompts that are served today.

Quantizing the drafter does not help here: KV cost follows the drafter's
*layers*, not its weights. The W4A16 DFlash2 drafter has the same 5 layers.

### 2. Prefix caching and the KV offload tier get disabled (vllm#41640, closed unmerged)

With DSpark on, the engine logs:

> Speculative decoding (method=dspark) is enabled but no KV cache group could be
> identified as the draft model's, so every group -- including Mamba groups
> [0..9] -- will be treated as a draft group. A Mamba group cannot satisfy the
> widened lookup window that implies, so prefix-cache reuse across requests will
> be disabled and any external KV offload tier will store without ever serving a
> hit.

and the scheduler adds `EAGLE/MTP draft attention groups [0..14] detected. The
trailing chunk of these groups will be excluded from offloading due to
volatility.`

This deployment cannot afford that: removing the fs secondary tier alone was
measured to **halve single-stream decode** (15.5 vs 31 tok/s, exact-revert
confirmed), and 91% of prompt tokens were prefix-cache hits over 30 days (ultraquant-2026-10-07.md, Workload).

Root cause, in `vllm/v1/core/kv_cache_utils.py` at our pinned `8a728663c`:
`_annotate_eagle_groups` identifies the drafter's KV group only via a
`non_causal_multi_token_decode` marker set exclusively on `MLAAttentionSpec`, or
a DeepSeek-V4-specific positional fallback. Neither can match a **GQA drafter on
a non-MLA target**, which is exactly Qwen3.8-27B (GQA + GDN, not MLA). When
neither fires, `_warn_if_unannotated_eagle_mamba` emits the warning above and
every group is treated as a draft group.

- vllm#41640 generalizes the annotation (`is_eagle` on `AttentionSpec`). Two
  maintainers reviewed it favorably; it then went stale (bot-marked 2026-08-24)
  with unresolved conflicts. **Closed unmerged 2026-09-08.**
- vllm#55622 (2026-09-07) hit the identical problem independently for GLM-5.3's
  GQA drafters and confirmed the same fix shape; withdrawn as fork-only.
- vllm#55519 (closed unmerged 2026-09-24) only suppresses a false-positive
  variant of the warning; it does not fix detection.
- vllm#55390 (merged 2026-09-22) extends the positional fallback to any `mtp`
  drafter; its own test keeps `eagle`, `eagle3` and `dspark` warning.

Not fundamental in principle — a fix was written and half-reviewed — but unfixed
for non-MTP drafters in anything shipped, and stalled for months. Treat as
fundamental for planning.

### 3. The tool-calling / structured-output wedge: fixed upstream, verified here 2026-09-12 (vllm#49002)

Opened 2026-07-18; the reporter ruled out the grammar-bitmask-cost theory by
direct benchmark. A maintainer closed it 2026-09-09 as fixed by vllm#55223
(eliminate full-history reasoning scans for structured outputs, merged
2026-09-08, in every nightly since).
This is the same failure class already recorded here for MTP: MTP + tool-calling
grammar wedged to ~0.2 tok/s (100x) under concurrent tool traffic; the
2026-09-12 MTP retest no longer shows it.

vllm#50924 (dspark grammar bitmask width mismatch, EngineCore crash) closed
2026-08-10, but that is a crash-on-first-request bug, not this wedge.

## n-gram / prompt-lookup — the one survivor

`use_eagle()` in `vllm/config/speculative.py` returns True only for
`("eagle", "eagle3", "mtp", "dflash", "dspark")`. That method gates both
`_annotate_eagle_groups` and the Mamba-group warning, so **`ngram` cannot trip
blocker 2 by construction**. The slot-accounting table in the same file lists
n-gram as needing zero additional KV-cache slots: no draft model, no draft KV
group, so blocker 1 does not apply either. It needs no change to
`maxModelLen` or `--kv-cache-memory`.

Config under test:

```sh
--speculative-config '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_min":3,"prompt_lookup_max":8}'
```

n-gram's win is entirely workload-shaped — it drafts by finding the current
suffix earlier in the context — so a single benchmark number would be
meaningless. Bracket it with two scripts that already suit the two ends:

- **floor**: `bench/walltime.py`, whose random-word "summarize" prompt has
  nothing meaningful to quote back. It is a *soft* floor, not a zero-hit case:
  the corpus is drawn from a 24-word vocabulary, so 3-gram repeats occur by
  chance and n-gram did fire (232 drafts, 63.2% acceptance). A true zero-hit
  floor would need a high-entropy prompt. Still the closer of the two to a
  request with nothing useful to look up.
- **ceiling**: `bench/spectest.py`, already written for exactly this question —
  a long code module the output must quote back verbatim, the coding-agent
  shape where prompt-lookup should hit hardest. It also verifies the model
  really quoted the source, so a fast-but-diverged run cannot be mistaken for a
  win.

Real tool-calling traffic lands between the two. The plausible win is output
that echoes spans already in the prompt — repeated JSON keys, schema fields,
argument names copied from the tool spec.

### Result

Measured with `num_speculative_tokens` 4 (`spec+1 = 5`, matching `parallelSlots`;
`cudagraph_capture_sizes [5,10,15,20,25]`). Blocker 2 confirmed absent by
measurement: no `draft group` or Mamba-group warning in the boot log, as the
`use_eagle()` source read predicted. KV pool 291,461 @ cap 246,944 = 1.18x, so
no cap change was needed and the 1.17x cliff is cleared.

The decisive run is the **real manifest edit** (`walltime.py` with
`PROMPT_FILE`): an 8,065-token prompt asking for a byte-for-byte modified copy
of this repo's own `qwen38-27b-vllm.yaml`, generating 1200 tokens. This is the
shape production actually sends — output that heavily quotes its input.

```text
rep  TTFT s  decode s  toks   tok/s  accept%  tok/step  ms/step
 1     8.58    19.45   1200   61.64     89.7     3.33      54.0
 2     8.67    19.02   1200   63.04     89.1     3.45      54.7
 3     8.68    19.94   1200   60.13     88.1     3.32      55.2
```

Flat across reps, unlike the synthetic prompt, because the amount of quotable
material is a property of the task rather than of luck.

n-gram's per-position acceptance is nearly flat where DSpark's decayed — synthetic
173/146/133/126, quote-back 86/74/73/68 (98%/86%/99%/93% conditional). It drafts
only when it finds a match, so it is silent rather than wasteful on the steps it
cannot help; what those steps still pay is the flat per-step tax below.

Two fixed costs the baseline did not pay, from the boot log, and the likeliest
components of that tax alongside the CPU-side suffix search:

> Async scheduling not supported with ngram-based speculative decoding and will
> be disabled.
> Model Runner V2 does not yet support ngram/ngram_gpu speculative decoding;
> using the V1 model runner instead.

Note on the ceiling harness: `spectest.py` reported `DIVERGED at char 0` because
the model emitted a "Here are handler_0 through handler_12..." preamble before
quoting; the quoted code itself matched. Its rate also folds prefill in, so its
number is not comparable to a `walltime.py` figure. Neither affects the verdict.

### Open before shipping

1. **The baseline is not yet apples-to-apples.** 31.73 was measured on the
   synthetic 4K prompt; 61.64 on the 8K manifest prompt. Decode rate should be
   near-independent of prompt content, but the 1.94x claim is only sound once
   the same manifest prompt is run with speculative decoding *off*. That costs
   one reboot and is the single highest-value remaining measurement.
2. **Concurrency is unmeasured.** Every number here is single-stream. Under
   concurrency a spec step batches `seqs x (spec+1)` tokens, which walks
   straight into the 5-row skinny W4A16 kernel limit (`MAX_SKINNY_BATCH_SIZE=5`);
   the win could shrink or invert there.
3. **The tool-calling wedge (blocker 3) is untested for n-gram.** It was
   observed with MTP. n-gram's drafts are not model-generated, so it may be
   unaffected — but production traffic here *is* grammar-constrained tool
   calling, and that is exactly the regime that wedged before.

Not worth tuning: `prompt_lookup_*` and raising `num_speculative_tokens`. At
~89% acceptance and 3.33 of a possible 5 tokens per step, the headroom left in
the drafting itself is small; the per-step tax is where the remaining cost is.

## MTP retest 2026-09-12 on nightly 70d3eb5: still rejected

`{"method":"mtp","num_speculative_tokens":3}`, capture sizes `[4,8,12,16,20]`,
AITER unified attention, `GPU_MAX_HW_QUEUES=1`, fast band verified. The
tool-calling wedge is gone (vllm#55223): grammar-constrained decode ran 43 /
86 / 88 tok/s aggregate at conc 1 / 3 / 5 against 31 / 66 / 98 without MTP.
Acceptance 66%, 2.0 accepted per draft.

Greedy equivalence, temperature 0, same nightly spec-ON vs spec-OFF
(`scratchpad/eqcapture.py`, five prompts): 4 of 5 diverge, at chars 9 / 97 /
928 / 39; only the short tool-call JSON is identical. The divergences are
fluent paraphrases (`departs 14:05; second departs` vs `departs at 14:05,
second at`), the same verify-step signature as the radiance run. And the
production shape still loses: 48K cached prefix, conc 2-5 aggregate 33 / 40 /
44 / 39 with MTP vs 45 / 51 / 62 / 71 without, on the same image and band.

## MTP retest 2026-10-24 on nightly 83c078d (vllm#61855 Triton fused GDN MTP decode): corrupts output

Question: does the ROCm Triton port of `fused_gdn_decode_post_conv_mtp` (vllm#61855, replaces ~8 small
kernels per GDN layer on the spec-decode step) change the 09-25 verdict? No: the correctness gate fails,
so no speed number counts. Image `vllm-openai-rocm:nightly@sha256:d92e7eb` (`0.32.1rc1.dev361+g83c078d95`),
unrouted clone of the production ISVC on a scratch kv-offload emptyDir, production and Hermes paused.

| config | KV pool | boot | `spectest.py` | chat, temp 0 |
| --- | ---: | --- | --- | --- |
| spec off (control) | 469,489 | | coherent, 18.85 tok/s | full function and explanation |
| MTP k=3 | 377,838 (-19.5%) | draft group `[3]` found, offload tier made, prefix caching on | degenerate: `Here` repeated, then `<think>` repeated | stops after 42 tokens, mid-function |
| MTP k=1 | 391,759 (-16.6%) | same | degenerate: `Here` repeated, then endless backticks | 300 tokens of backticks |

Acceptance: k=3 50/1,170 draft tokens (4%, 09-12 saw 66%), k=1 190/509 (37%). `spectest.py` always prints
`DIVERGED at char 0` here because the model prefixes `Here are...`; read the output, not that line. Cause
unknown (new kernel, the config overlay below, or elsewhere in the build); not isolated.
vllm#55390 holds: the MTP draft group is identified and the offload tier is created.

Clone recipe: `config.json` from the model dir with `re:.*mtp.*` appended to `quantization_config.ignore`
(ConfigMap subPath-mounted over `/models/<hash>/config.json`, else boot dies on `fc.weight`); `--speculative-config`
MTP; `--kv-cache-memory` 8.5 GB minus the 849 MB `model-mtp-bf16.safetensors`; cudagraph capture sizes
multiples of k+1 up to `parallelSlots * (k+1)`. Pause with `flux suspend hr hermes`, scale Hermes to 0,
`flux suspend ks llmkube-models` and `replicas: 0` on the production ISVC; restore in reverse.

DFlash2 and DSpark were not rerun. The only relevant commit in the range is vllm#54163 (stops dropping the last
prefix-cache block), which touches neither the drafter KV tax, the FP8 DFlash2 load failure nor the DSpark
sampler crash.

## DFlash2 test 2026-10-27 on nightly 0c030ba: cannot coexist with UltraQuant

Question: with the draft-group detection rewritten (vllm#61455: explicit draft-layer tags; dflash and dspark
skip the trailing-block drop, `config/speculative.py:2091`) and a 1.19 GiB drafter published
(`syvai/Qwen3.8-27B-DFlash2-W4A16@4d30ec73`, a GPTQ requant of the z-lab drafter, 5 sliding-window layers), is
DFlash2 viable? Image `0.32.1rc1.dev602+g5d35ce393`, unrouted clone, production and Hermes paused 41 min.
The drafter suits our target: on a Swift-1.5 W4A16 derivative it accepts 3.6 tokens per step (akumaburn card).

With the production KV (`ultraquant_4bit`), DFlash2 does not boot:

| drafter KV | result |
| --- | --- |
| fp8, 262K cap | refused: 11.55 GiB KV needed vs 6.41 GiB available (est. max 128,544) |
| fp8, 120K cap | pool 126,924 tokens (-73%); hybrid block 1536 -> 1648 breaks `--prefix-match-unit 64`; with 16 or none: `block stride 3375104 != page 1793024`, the drafter page pads every target page (vllm#62558) |
| inherit `ultraquant_4bit` | no ROCm backend for head_size 128 (`platforms/rocm.py:876`) |

The drafter also reserves KV for the full sequence although it keeps a 2048 window (vllm#55800).

To measure the method anyway, both arms ran fp8 target KV, `TRITON_ATTN`, 120K cap, `--prefix-match-unit 16`
(block 880), drafter k=7:

| | DFlash2 k=7 | spec off | production (UltraQuant) |
| --- | ---: | ---: | ---: |
| KV pool (tokens) | 142,862 | 248,832 | 469,489 |
| decode tok/s, 4K (`walltime.py`) | 48.3 | 34.4 | |
| decode tok/s, 40K | 37.2 | 31.2 | |
| 40K cold TTFT (s) | 63-77 | 58.5 | 32.8 (10-26 `prodshape.py`) |
| `concsweep.py` agg tok/s, 1/2/4/8 | 32.7/57.9/97.7/185.6 | 33.1/60.8/95.8/148.6 | |

- Acceptance: 4.05 tokens per verify step on chat, 7.5 on the verbatim echo, 2.8-3.3 on synthetic text.
  A verify step costs 65-78 ms against 29-32 ms for a decode step.
- Correctness: the full `spectest.py` output (not its 200-char head) matched the source verbatim, and
  three temperature-0 chats stayed coherent. Greedy text diverges from spec-off at chars 5/15/436/1172.
  Rescored on the spec-off target, the spec-on token was the second choice 0.25-0.38 nats behind
  (near-tie flips, vllm#54928), not a garbage token. GSM8K, tools and agree were not run.
- `longctx.py` reported 16.8 tok/s for the spec arm; per the method note above that figure is invalid.

Verdict: the fp8 stack trades two thirds of the KV pool, the 262K context and about twice the prefill time for
+19-40% decode. Retest at 262K with UltraQuant once vllm#62558 (or a head-128 UltraQuant backend) and
vllm#55800 land; the Hermes watch flags both.

## Unrelated finding: the nightly regression persists

**Superseded 2026-09-12:** this was the gfx1201 per-process fast/slow band
(ROCm/ROCm#6347), not a vLLM commit; see `gfx1201-fast-slow-band-2026-09-12.md`.
The version/ruled-out data below is still valid, the attribution is not.

The `vllm-openai-rocm:nightly` decode regression that forced the pin to
`0d07767` is **still present in the 2026-09-07 build** (`74d4a95`,
`v0.28.1rc1.dev472+gd9105ea80`), measured the same way:

| image | built | vLLM | tok/s @4K |
| --- | --- | --- | --- |
| `0d07767` (pinned) | 09-04 | dev388+g8a728663c | 31.73 |
| `74d4a95` (latest) | 09-07 | dev472+gd9105ea80 | 16.05 |

Exactly 1.98x, flat across 4 reps, TTFT unchanged — decode-only.

Ruled out as causes, by direct comparison of the two images: torch
(`2.12.0+git6bbd260`), triton (`3.7.1+gitf0b55c07`), numpy, transformers and
ROCm 7.2.3 are identical; the AITER Python tree hashes identically
(`72ccfebb…`, 620 files both sides); both boots select the same
`GDN decode kernel: triton`; and both mounted patches applied in both
(`LDS_CAPACITY_ELEMENTS 32768 -> 39321`, `supports_kv_connector -> True`,
`RDNAHybridW4A16LinearKernel`, `ROCM_AITER_UNIFIED_ATTN`). The KV pool was
larger on the new image (304,808 both, 1.23x), so pool sizing is not a variable.

So the delta is purely the 84 vLLM commits between `8a728663c` and the 09-05
build cutoff. Suspects that touch paths this config actually runs:

- `8277c42e4` [Perf] pin async h2d copies (#55202) — touches `rocm_aiter_fa.py`,
  our attention backend.
- `5690b02c0` online quantization with partially pre-quantized checkpoints
  (#51392) — touches `layers/linear.py` + `quantization/base_config.py`.
- `6cbb3c154` `[Perf][GDN]` cudagraph-capture metadata without a device sync
  (#55404) and `874df9373` mamba state for padded prompt tails (#55178).

**Eliminated:** `a69e75b9b` "Fast Start" (#54921) — opt-in via
`load_format="ipc_cache"`, and its `linear.py` / `base_config.py` changes are
additive capability flags only. Also eliminated: the LDS-gate patch target
`rdna_hybrid_w4a16.py` is untouched in the whole window, so the patch still
applies and is not the cause.

Nothing between the 09-05 and 09-07 builds reverts or fixes any of the above.
Keep the pin.

## Reproducing

```sh
# baseline / non-spec only -- ITL-derived
python3 docs/llm-hosting/bench/longctx.py <port> qwen-3.8 4000 4

# anything with speculative decoding on -- wall-clock decode rate.
# Soft floor only: this prompt draws on a 24-word vocabulary, so 3-gram
# repeats occur by chance and n-gram does fire (232 drafts, 63% acceptance).
python3 docs/llm-hosting/bench/walltime.py <port> qwen-3.8 4000 4

# a real workload shape -- pass any prompt file, plus a generation length
python3 docs/llm-hosting/bench/walltime.py <port> qwen-3.8 0 3 prompt.txt 1200

# grammar-constrained tool calling, optionally concurrent and with a system
# prompt: the regime that wedged MTP. PRESET is `deployment` or `vmcp`.
python3 docs/llm-hosting/bench/toolbench.py <port> qwen-3.8 5 2 system.txt vmcp

# n-gram CEILING: verbatim quote-back, and checks the output really matches
python3 docs/llm-hosting/bench/spectest.py <port> qwen-3.8
```

`spectest.py` and `longctx.py` already existed; `walltime.py`, `toolbench.py`
and `_metrics.py` are added here. All do an idle preflight and refuse to count a
dirty rep, matching the methodology the rest of this directory uses.

`walltime.py` exists because `longctx.py` derives tok/s from the ITL counter,
which is invalid under speculative decoding, and because `spectest.py` folds
prefill into its rate and runs a single rep. `toolbench.py` exists because
nothing here exercised tool-calling grammar under concurrency. `_metrics.py`
holds the single `/metrics` scrape the two new scripts share.

**`spectest.py` is the only one that checks correctness, and it is the one that
matters.** The others measure speed, and speed alone is what made a corrupting
config look like a 2x win.
