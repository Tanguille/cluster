# Alternative engines on one R9700, 2026-11-18

Two candidates to replace radiance 1.17.0 (qwen-3.8, MTP k6_v4, 8 slots, 262144 context; see
[radiance-2026-11.md](radiance-2026-11.md)): vllm-sly-radiance 1.12.0 and saber-engine. Both lost. Numbers only: sly had no licence
file on 2026-11-18, so any run of it stays a private eval and none of its code is stored here.

## Method

- Live swap of `qwen38-27b-vllm` (ns `ai`) on the single R9700 in control-1, Flux-fenced with
  `kustomize.toolkit.fluxcd.io/reconcile=disabled` and restored from a saved copy of the ISVC. One GPU pod at a time, so prod was
  down during each arm.
- Benches as in the radiance doc: `bench/prodshape.py --levels 1,2,4,8 --warmup --runs 2` (default prefixes 40K, 16K, 64K, 40K,
  98K cycled, 3 turns; the `16K` shape is `--prefixes 16k,16k,16k,16k`), `bench/walltime.py`, `bench/qualitygate.py` tools and gsm8k.
  Run through `kubectl port-forward` (exec into the prod pod is denied); every sweep finished in under 35 min with 0 errors, so the
  stall the prodshape docstring warns about did not occur. `--tpw` replaces the missing `/tokenize` on saber. `--allow-busy` was passed
  for the saber runs and the radiance 16K run because agent traffic could be on the pod, so those runs are not idle-gated.
- Checkpoints differ per engine (ours Swift 1.5 W4A16-AWQ, sly the heretic Swift 1.5 MXFP4 GPTQ, saber MACaQ MXFP4 of base
  Qwen3.8-27B), so quality scores are absolute, not an engine delta.
- Not run for either candidate: vision, needles, RULER, soak. sly also skipped GSM8K and tools, because the 8-session gate failed first.

## Baseline: radiance 1.17.0, same day

prodshape agg tok/s, 2 runs, on the live pod while agent traffic was also on it (the radiance doc shows 104 at 4 and 8 sessions on an
idle pod):

| Sessions | 1 | 2 | 4 | 8 |
| --- | --- | --- | --- | --- |
| Default prefixes | 49-51 | 84-89 | 96-97 | 96-98 |
| 16K prefixes | 52 | 77 | 111 | not run |

Cold prefill about 1,930 tok/s (default) and 2,150 tok/s (16K). Pool 535K tokens. Quality: GSM8K 146-147/150, tools 12/12.

## vllm-sly-radiance 1.12.0 (github.com/SlyBase/vllm-sly-radiance, image `ghcr.io/slybase/vllm-sly-radiance:1.12.0-rocm10.1`)

Load-bearing settings: `--kv-cache-dtype fp8` or `int4_per_token_head` (kv4, TetraAttention), drafter, `--gpu-memory-utilization` 0.93 (2.2 GiB
free), chunk 2048 or 4096, `--mamba-ssm-cache-dtype`, context 262144 unless noted (arm 5 was capped at 245,000), Cactus delta 0 (lossless) in arms 2-7.

| Arm | Config | Pool (tokens) | prodshape 1 / 2 / 4 / 8 |
| --- | --- | --- | --- |
| 1 | fp8, syvai W4A16 drafter, chunk 2048, hand-rolled flags (booted at 262144 and 0.93) | 375,686 | 84 / 97-116 / 93-112 / 11 |
| 2-5 | kv4 or fp8 with the launcher's default (tcclaviger FP8) drafter, assorted ssm and libr4d variants | 247K-435K | 13-22 / 23-38 / n/r / n/r |
| 6 | kv4, syvai W4A16 drafter, bf16 ssm | 471,160 | 43-55 / 54-68 / 70-76 / 10.6 |
| 7 | arm 6 at utilization 0.97, ctx 131072 | 463,886 | n/r / n/r / n/r / 11.4, 2 preemptions |

Findings:

- **8 sessions collapsed in all three arms that reached 8 (1, 6, 7).** Arm 6: TTFT p50 97 s, inter-token p90 1.3 s, prefix hit rate 9%.
  Radiance holds 535K with 0 preemptions at 8, sly had 375K to 471K, so pool size is not ruled out. Arm 7 changed utilization and context
  together, so it does not isolate the cause. Prefix retention under load is the symptom, the cause is untested.
- **fp8 with chunk 4096 and the launcher's 2 GiB drafter could not boot 262144 context at utilization 0.93** (arm 5 control): it needs
  8.84 GiB of KV and has 8.39 GiB. Capped at 245,000 tokens the pool is 246,975. Arm 1 (chunk 2048, 1.2 GiB drafter) did boot 262144 with
  a 375,686 pool, so the boot limit depends on chunk size and drafter, and it is tight either way.
- **kv4 gave 1.76x the pool of fp8 at the same launcher settings** (433,411 in the kv4 bf16-ssm arm of 2-5 against 246,975 in arm 5, both
  chunk 4096 and the launcher drafter; context 262144 against 245,000). It looks slower: arm 6 (kv4) 70-76 tok/s at 4 sessions against
  arm 1 (fp8) 93-112, but the two share the syvai drafter and differ in chunk, attention backend and cache mode as well, so the
  gap is not isolated to the KV format.
- **The launcher's default drafter is consistent with a mismatch for our checkpoint.** Arms 2-5 ran about 1 accepted token per step; the
  syvai drafter in arm 6 fixed it.
- **The upstream launcher does not fit the 1.12.0 image.** Its patch prelude reported "anchor matched 0x" on 10+ patches and a config copy
  had no target, probably because the image already bakes them (inferred). Run the image directly with its documented env and flags.
- **Launcher single-card profile `--mamba-ssm-cache-dtype float16`** logged a fallback to the Triton GDN path and decode was slow. bfloat16
  was used from arm 4 on.
- Cold prefill about 3,000 tok/s (arm 1).

Verdict: rejected. On fp8 arm 1 read higher than radiance at 1 to 2 sessions (measured with prod down and a different checkpoint, against a
baseline taken with agent traffic on the pod, so the gap is not isolated to the engine), but fp8 booted 262144 only in the lighter arm 1
config (375,686 pool, and the same utilization failed with chunk 4096), kv4 gives the larger pool but read slower, and none of the 8-session
runs held (11 tok/s in arm 1), which is the basis for rejection.

## saber-engine (github.com/GGZ14/saber-engine, commit `ee99282`, MACaQ MXFP4 single-card pack, DFlash2 drafter, TP=1)

Built with `cargo build --release -p saber-cli` in `rocm/dev-ubuntu-24.04:7.2.4` (Job on control-1); packs `qwen38-27b-macaq7-bc3-tp1` from
`Launch80/Qwen3.8-27B-MACaQ-MXFP4-SABER` and `qwen38-dflash2-drafter-quarkhead-tp1` from `Launch80/Qwen3.8-27B-Quark-AWQ-MXFP4-SABER`; boot 17.6 s. Needs `--engine-ledger today --engine-budget-mib 29500`: the default `lean` ledger leaves 256 MiB
free and starves Jellyfin (needs 1.82 GiB).

| Config | Result |
| --- | --- |
| 4 seqs, ctx 65536 | boots: 118,640 KV tokens, 4 GDN state slots, one 64K session admitted, 3,858 MiB unreserved |
| 8 seqs, ctx 65536 | refused at boot: the ledger leaves 3,170 KV blocks after 5,289 MiB of GDN state and 1,024 MiB headroom, one sequence needs 4,097 |
| 262144 ctx | not possible on one card (its README: at most 176,128 with 1 sequence) |

prodshape on 4 seqs / ctx 65536, 16K prefixes, 2 runs, against radiance on the same shape:

| Sessions | saber agg tok/s | radiance agg tok/s | saber cold prefill | radiance cold prefill |
| --- | --- | --- | --- | --- |
| 1 | 62 | 52 | 2,830 | 2,200 |
| 2 | 75-87 | 77 | 2,800 | 2,110 |
| 4 | 113-120 | 105-117 | 2,740-2,800 | 2,100-2,120 |

With the default prefixes (40K first session): 1 session 61 and 45 agg tok/s, 2 sessions 25 and 15. A 118K pool holds one 40K session plus
its turns, so the second session likely forces re-prefill (not isolated). A single decode probe on a 40K synthetic prompt read 3.9 tok/s,
cause unknown; a nominal 60K-token `walltime.py` prompt got HTTP 400 (response body not captured; likely the prompt plus 512 generated tokens
over the 65536 limit, not verified).

Quality on the 4-sequence config: GSM8K 147/150 and tools 12/12 single and with 5 concurrent, against radiance 146-147/150 and 12/12.
saber exports `saber:*` metrics and has no `/tokenize`; it has no KV format below fp8 (fp8 and bf16 only).

Verdict: rejected. At 16K prefixes and up to 4 sessions it matches or slightly beats radiance and prefills about 30% faster, but it cannot
hold our 40K+ sessions, 8 slots or 262144 context on one card.

## Conditions to re-test

- sly: a release that keeps 8 sessions of 40K to 98K prefixes at agg tok/s of at least 96 with 0 preemptions, with a kv4 decode close to fp8.
- saber: a KV format smaller than fp8, or a lean-ledger mode that keeps 2 GiB free, with an 8-sequence pool above 500K tokens.
