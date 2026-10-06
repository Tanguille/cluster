# vLLM nightly bump bench, 2026-09-29

qwen38-27b-vllm on control-1 (R9700, gfx1201), Swift AWQ, `parallelSlots: 5`.
Bump `29468dde8` (image `8dd5054`; torch 2.12.0, triton 3.7.1, aiter 0.1.22.post1)
to `36768d1bf` (image `50b4f2a`; torch 2.13, triton 3.8, aiter 0.1.23; ROCm base
7.2.3 unchanged), 169 commits. Versions from `pip list` in the pod and the
`docker/Dockerfile.rocm_base` diff.

**Verdict: no config change.** All three out-of-tree patches and the AITER backend stay.

## Method

Suspend `llmkube-models` and `hermes` (ks and HR), scale Hermes to 0. Other
litellm consumers stay up, so only `longctx.py` and `toolbench.py` gate on idle
(`wait_idle`); `concsweep.py` does not. Its 4-rep pstdev is <= 0.8 tok/s except
B M3 (2.3), D M2 (1.5) and D M5 (3.0), so no rep looks contaminated. Per arm, patch the InferenceService
live (`kubectl -n ai patch inferenceservice qwen38-27b-vllm`) and roll the pod:

| arm | change |
|---|---|
| old | `spec.image` = `8dd5054` digest |
| A | none (new image) |
| B | `--attention-backend TRITON_ATTN`; replace the liveness probe with `/bin/true` before starting, it killed the pod mid-arm once (see Findings) |
| C | mount a copy of the `lds-gate-patch` ConfigMap without the `PATCHES` aiter entry |
| D | same copy, `_install_small_m_triton_config(module)` replaced by `pass` |

Restore with `flux resume` (git wins) and delete the temporary ConfigMaps.
Per arm, from `docs/llm-hosting/bench/`, with `kubectl port-forward pod/<pod> 18000:8000`:

```bash
python3 longctx.py 18000 qwen-3.8 4000 3            # band check, not reported, expect ~32.5 (fast)
for i in 1 2 3 4 5 6; do python3 concsweep.py 18000 qwen-3.8 1,2,3,4,5; done   # first 2 discarded
python3 longctx.py 18000 qwen-3.8 4000 3            # reported 4K decode
python3 longctx.py 18000 qwen-3.8 50000 3           # 63,894 tokens, no 5th arg: cold prompts
python3 toolbench.py 18000 qwen-3.8 5 3 - vmcp
```

Mean and pstdev over the 4 kept concsweep reps were computed by hand from the
output. Boot checks per arm: `[lds-gate-patch]` lines, `GPU KV cache size`,
backend line.

## Results

Warm, fast band, Hermes paused. KV pool 304,808 tokens in every arm except B (304,956).

| arm | M1 | M2 | M3 | M4 | M5 | 4K decode | 64K decode | 64K TTFT |
|---|---|---|---|---|---|---|---|---|
| old image, prod config | 31.3 | 56.4 | 73.7 | 97.1 | 117.2 | 32.55 | 29.33 | 63 s |
| **A: new image, prod config** | 31.3 | 56.9 | 74.3 | 97.8 | 117.4 | 32.49 | 29.33 | 59 s |
| B: `TRITON_ATTN` | 32.0 | 58.6 | 74.9 | 99.0 | 120.0 | 32.60 | 24.08 | 293 s |
| C: no aiter `num_stages=1` | 31.2 | 56.4 | 73.7 | 96.9 | 116.9 | 32.18 | 22.22 | 60 s |
| D: stock W4A16 Triton tile | 30.6 | 54.6 | 67.1 | 86.3 | 105.0 | 32.49 | 29.33 | 59 s |

Toolbench (n=3, spread 73-90 within one arm) was ignored.

## Decisions

| Item | Decision | Basis |
|---|---|---|
| Nightly bump | Keep | A = old within 1% at every M, same 64K decode, same KV pool. |
| AITER backend | Keep | B: +2-3% at M<=5, 64K prefill 5x slower, 64K decode -18%. 87% of prompts exceed 20K. Differs from the 1.9 tok/s / 456 s row in `gfx1201-fast-slow-band-2026-09-12.md` (pinned build, JIT recompiles mid-serving); B's three 64K reps agree within 1%, no JIT signature. |
| `attn_3d` `num_stages=1` patch | Keep | C: -24% at 64K. aiter 0.1.23 (#4868) only adds a D/dtype axis; `gfx1201` `D_LEQ_256.DT_any_fp8` still ships `num_stages: 2`. |
| M<=32 Triton tile patch | Keep | D: -10 to -13% at M=3..5. D's M5 reps span 101.7-109.3. |
| W4A16 LDS gate patch | Keep | vllm#52619 still open ([drop condition](gfx1201-decode-findings-2026-09-02.md)). |
| Spec decode | Still rejected, not benched | vllm#39273 open, vllm#41640 closed unmerged, nothing in range addresses [the blockers](spec-decode-rejected.md). |
| TurboQuant | Still not viable, not benched | vllm#53410 open, no `gfx1201` MHA config in aiter 0.1.23 ([watch list](gfx1201-decode-findings-2026-09-02.md)). |

## Upstream range

| Commit | Effect |
|---|---|
| #50605 torch 2.13, triton 3.8; #58867 aiter v0.1.23 | none (A vs old) |

Also in range, no measurable effect: #58762 (GDN metadata reuse across KV
groups), #58400 (FULL decode graph for one-token prompt tails), #58947
(scheduler `kv_holding_waiting`), #58430 (profiling fragmentation, inert with
`--kv-cache-memory`). Untouched: `vllm/v1/kv_offload`, W4A16 kernel files.

## Findings

- Arm B's pod was killed once by the [band liveness probe](gfx1201-fast-slow-band-2026-09-12.md)
  ("slow band: 0/8 solo samples fast") while bench throughput read fast (32.7
  tok/s at 5K). Afterwards `mem_busy_percent` read 79 at solo decode on
  `TRITON_ATTN`, above `busy_min=60`. Cause unexplained, n=1.
- The manifest's "~27% hit rate under real traffic" (also
  `spec-decode-rejected.md`, "27-40%") does not match the counters. Pod-lifetime
  (34 h) `vllm:prefix_cache_hits_total / prefix_cache_queries_total` = 68.7M /
  75.6M = 91%. `external_prefix_cache_hits_total / external_prefix_cache_queries_total`
  = 1.48M / 6.83M = 22%; the external queries equal the local misses, so 22% is
  the offload-tier hit rate on local misses, not on all prompts. The manifest's
  "external hit 0.70" (7d window, KV tier comment) uses another window and was
  not re-measured.
