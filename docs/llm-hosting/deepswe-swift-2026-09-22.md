# 2026-09-22: DeepSWE v1.1 baseline, Swift-Qwen3.8-27b per thinking level

Swift-Qwen3.8-27b W4A16 AWQ on one R9700 (gfx1201), vLLM ROCm nightly `5eb04115`,
engine `qwen38-27b-vllm` (fp8 KV, CPU + fs offload tiers, ~247K context).
Same 20 tasks at four thinking levels, runs 2026-09-19 to 09-22.

## Scores

| level | solved | rate | Wilson 95% | near miss | mean F2P | 3 h timeouts | loops | 250-step cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| thinking off | 0/20 | 0% | 0-16% | 0 | 0.00 | 0 | 14 | 20 |
| low | 5/20 | 25% | 11-47% | 6 | 0.67 | 1 | 0 | 0 |
| medium | 5/20 | 25% | 11-47% | 9 | 0.81 | 0 | 0 | 1 |
| **xhigh** | **9/20** | **45%** | 26-66% | 3 | 0.65 | 6 | 0 | 0 |

Reward is binary: all fail-to-pass (F2P) tests pass and no pass-to-pass test
breaks. Near miss = failed with F2P >= 0.85. Loop = last command repeated > 20
times.

## Cost per trial

| level | steps (mean) | minutes (median) | output tok | input tok | peak ctx mean / max | peak > 131K |
| --- | --- | --- | --- | --- | --- | --- |
| thinking off | 250 | 21 | 25.7K | 16.7M | 106K / 187K | 2 of 11 |
| low | 123 | 96 | 82.3K | 15.3M | 176K / 181K | 4 of 4 |
| medium | 113 | 64 | 69.9K | 9.4M | 128K / 224K | 12 of 20 |
| xhigh | 117 | 129 | 82.9K | 12.0M | 160K / 228K | 16 of 20 |

Low's steps, tokens and context cover only the 6 trials run after the
2026-09-19 reboot (4 for context). Minutes are medians: trials spanning engine
restarts or laptop pauses read 5-10 h wall. Input tokens are mostly prefix-cache
hits.

## Per task

`ok` solved, `n/m` F2P passed on a failure, `T` 3 h timeout, `1/3` exactly one
of those three solved at low (per-task split lost in the 09-19 `/tmp` wipe).

| task | off | low | medium | xhigh |
| --- | --- | --- | --- | --- |
| arcane-drift-detection-baselines | 0/82 | ok | ok | ok |
| bandit-incremental-cache-control | 0/88 | 83/88 | 84/88 | ok |
| cattrs-partial-structuring-recovery | 0/69 | 66/69 | 66/69 | 65/69 |
| csstree-shorthand-expansion-compression | 0/79 | 66/79 | 69/79 | 70/79 |
| dynamodb-toolbox-conditional-attribute-requirements | 0/31 | ok | 21/31 | ok |
| helm-array-merge-strategies | 0/47 | 1/3 | 44/47 | 35/47 |
| helm-unified-manifest-stream | 0/5 | 3/5 | 3/5 | T 0/5 |
| koota-composite-trait-aspects | 0/51 | 1/3 | 45/51 | T 0/51 |
| koota-deferred-mutation-buffer | 0/71 | 17/71 | 61/71 | T 0/71 |
| koota-entity-snapshot-rollback | 0/84 | 81/84 | 82/84 | ok |
| kysely-window-grouping-helpers | 0/254 | 0/254 | 0/254 | 0/254 |
| obsidian-linter-scoped-ignore-markers | 0/33 | 0/33 | ok | ok |
| python-statemachine-state-data-scoping | 0/72 | 70/72 | ok | T 0/72 |
| query-persist-restored-query-state | 0/8 | 8/8 (P2P broke) | 8/8 (P2P broke) | ok |
| scriggo-method-declarations | 0/48 | T 0/48 | 0/48 | T 41/48 |
| sql-formatter-bigquery-pipe-formatting | 0/26 | ok | ok | ok |
| sqlfmt-create-table-ddl-formatting | 0/32 | 29/32 | 25/32 | T 0/32 |
| termenv-preserve-ansi-resets | 0/35 | 1/3 | 30/35 | 23/35 |
| wazero-multi-module-snapshots | 0/78 | ok | ok | ok |
| yaegi-go-embed-directives | 0/38 | 0/38 | 31/38 | ok |

- Solved at some level: 11/20. Never: cattrs, csstree, helm-unified,
  koota-deferred, kysely (0/254 at every level, likely the task or its env),
  scriggo, sqlfmt.
- xhigh vs medium, paired: 5 xhigh-only solves (bandit, dynamodb, koota-entity,
  query-persist, yaegi), 1 medium-only (python-statemachine, timed out at
  xhigh). Exact McNemar p ~0.22: suggestive, not proven.
- xhigh's 6 timeouts were still working (51-193 steps, no loops); the other 5
  failures submitted. 45% is a lower bound under the 3 h cap.
- Thinking off: 20/20 hit the step cap, 14 dead-looped, zero F2P tests passed on
  any task, with Qwen's non-thinking sampling (temp 0.7, top_p 0.8, presence 1.5).

## Reference scores (different setups)

| model / setup | DeepSWE | harness | source |
| --- | --- | --- | --- |
| Swift W4A16 xhigh (this run) | 45 (26-66) | mini-swe-agent, 20 tasks, 1 run | measured |
| Swift W4A16 medium / low (this run) | 25 / 25 | same | measured |
| Qwen3.8-27B base BF16 | 42.2 | vendor agent harness, 113 tasks, pass@1 over 4 runs, 256K ctx, temp 1.0 / top_p 0.95 | Qwen model card |
| Qwen3.7-Plus | 14.2 | same as above | Qwen model card |
| Qwen3.6-27B | 13.3 | same as above | Qwen model card |
| Qwen3.8-Max xhigh | 57.5 | mini-swe-agent, ~111 steps/task | DeepSWE leaderboard |
| Qwen3.8-Flash-Next | 58.7 | | DeepSWE leaderboard |
| GLM-5.2 | ~44 | | DeepSWE leaderboard |
| Gemini 3.5 Flash | 36.1 | | DeepSWE leaderboard |
| DeepSeek V4 Pro | ~63 | | DeepSWE leaderboard |
| GLM-5.3 | ~67-69 | | DeepSWE leaderboard |
| top entries (GPT-6 Astra, Gemini 3.8 Flash) | ~74 | | DeepSWE leaderboard |

Swift vs base on the Swift model card (not DeepSWE): Terminal-Bench 2.1 65.84 vs
66.74, GPQA 88.28 vs 88.38, 26-46% fewer tokens. The Qwen card also lists
SWE-bench Pro 61.7, Terminal-Bench 2.1 73.0 and QwenSWEBench 79.0 for the base.

- 42.2 sits inside the xhigh interval: Swift + W4A16 shows no visible agentic
  loss against the published base number. Resolving a 1-2 point gap needs the
  full 113 tasks and several runs.
- Qwen3.8-Max is the closest like-for-like ceiling (same harness, similar step
  count): 57.5 vs 45.

## Implications

- Agentic coding: xhigh. Cost per solve is about medium's (median
  129 min x 20 / 9 = 4.8 h vs 64 x 20 / 5 = 4.3 h) for ~1.8x the solves.
- Interactive default: medium. Same score as low, twice as fast per trial as
  xhigh, most near misses.
- Never route agentic work to thinking off.
- Context: peak > 131K in 12/20 medium and 16/20 xhigh trials, max 228K.
  Prod's 246,944 covers every trial. The Paiton candidate (#5200) at
  `--max-model-len 131072` would truncate most agentic runs; an agentic A/B
  needs >= ~230K, otherwise judge it on chat and throughput only.

## vLLM bump 0821cdd -> 5eb0411

Idle engine after the 09-22 control-1 outage restart, same scripts as the
0821cdd baseline. Both fast band (5K decode ~32 tok/s; slow band reads ~16).

| bench | 0821cdd | 5eb0411 | delta |
| --- | --- | --- | --- |
| `longctx.py` 5K (5,129 tok) prefill | 1289 tok/s | 1303 tok/s | +1.1% |
| `longctx.py` 50K (63,894 tok) prefill | 992 tok/s | 999 tok/s | +0.7% |
| `longctx.py` 5K decode M=1 | 32.41 | 32.37 | -0.1% |
| `longctx.py` 50K decode M=1 | 29.12 | 29.17 | +0.2% |
| `concsweep.py` agg tok/s M=1..5 | 30.44 / 55.27 / 71.54 / 95.77 / 116.02 | 30.95 / 56.95 / 74.29 / 98.35 / 119.37 | +1.7 / +3.0 / +3.8 / +2.7 / +2.9% |

Neutral, keep `5eb04115`.

## Method

- DeepSWE v1.1 via pier 0.3.1 + mini-swe-agent, 20 tasks (`--n-tasks 20
  --sample-seed 0`), `-n 2`, `step_limit 250`, 3 h agent timeout, one run per
  level. Effort via `chat_template_kwargs.reasoning_effort` with the model's
  default sampling; thinking off via `enable_thinking: false`.
- Infra failures (image builds, `NonZeroAgentExitCodeError` from vLLM restarts)
  were rerun, not scored; `AgentTimeoutError` counts as a real 0. A timed-out
  trial is still verified against what the agent left.
- The engine served prod traffic throughout. Incidents: host reboot wiped
  `/tmp` on 09-19, laptop pauses, two vLLM bumps, the #5197 seccomp restart,
  the 09-22 control-1 RBD outage.
- n = 20 gives ~+/-20 point intervals. Next: four runs or the full 113 tasks;
  rerun xhigh's six timeouts at `-n 1` with a 6 h cap.
