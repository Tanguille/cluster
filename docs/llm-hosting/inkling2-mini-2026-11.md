# Inkling 2 Mini on radiance (2026-11)

`inkling-2-mini` replaced `qwen-3.8` as the local model on control-1's R9700 (gfx1201, 32 GB). The same radiance process serves `qwen-3.8` on demand: see [qwen-3.8 on demand](#qwen-38-on-demand).

## Engine and model

- Image: `ghcr.io/tanguille/radiance:attnrel-<sha>`, our own build. Upstream support is radiance PR [#584](https://codeberg.org/StillDeadcode/radiance/pulls/584) (merged) and [#608](https://codeberg.org/StillDeadcode/radiance/pulls/608) (open; it replaced #585/#586). The build is the #608 branch plus two local changes:
  - host tests on, minus six that fail only inside BuildKit (`TEST_EXCLUDE`: `avx_check` needs io_uring, plus `dashboard_test`, `gbnf_test_shard2`, `quant_ggml_test`, `shipped_plugins_test`, `artifact_sums`);
  - the `inkling-perf` patch: NVFP4 MoE takes the prefill tiles only from 16 routed rows an expert (`RADIANCE_NVFP4_PF_ROWS` overrides), and the sort writes `moe_gather_inv`'s slot table. +10% aggregate at 8 sessions in one run; upstream as [#613](https://codeberg.org/StillDeadcode/radiance/pulls/613), stacked on #608.
  - local engine work, not upstream (private branches, no source in this repo): the replay-audit fix (relative attention declares live prefill splits; without it a mixed step could stop the engine), a short-verify band so 8-row verifies take the packed kernel, a Markov score grid that shares fold tiles, a compact packed-verify workgroup with more KV splits, a direct-from-memory relative-attention kernel (`RADIANCE_REL_VARIANT=2`), and a draft policy that skips the proposal below 4 decode rows only when a plain step wins. On top, spizzerp's [Tangenius/radiance#1](https://codeberg.org/Tangenius/radiance/pulls/1): `RADIANCE_REL_COMPACT_MIN=1` sends one or two sessions' long verifies to the compact band.
  - Built with rootless BuildKit from the upstream Dockerfile (ccache mount). Move to the upstream image once the PRs merge.
- Model: [gostar77/inkling2-mini-i8-dspark-radiance](https://huggingface.co/gostar77/inkling2-mini-i8-dspark-radiance), a ready `.rad` (int8 trunk, NVFP4 experts, DSpark drafter, vision tower). Its layout follows the engine build, so the revision is pinned and moves with the image.

## Config and why

The engine takes only `--config`: `kubernetes/apps/ai/llmkube/models/resources/radiance.yaml`, mounted from a ConfigMap, with each key's reason beside it. A change does not roll the pod; restart it. The adaptive draft window beat a fixed one by 7-14%.

## Measurements (2026-11-23, 210 W cap)

- KV pool: 501K tokens at 8 slots, 466K at 16 (the drafter's own KV takes 2.9 GiB).
- BetterBench 0.6.1 vs qwen 1.17.0: single stream 20-60% faster by category, cold prefill ~5,000 vs ~2,100 tok/s; aggregate C1/2/4/8 119/165/197/245 vs 89/161/218/254.
- Quality: GSM8K 143-147/150, tools 12/12, vision 2/2, needles 9/9 at 20K/60K/200K through `/v1/chat/completions`. Raw `/v1/completions` without the chat template invents the code: always serve through the chat API.
- prodshape (production-shaped agent sessions, ~52K-token prefixes), aggregate tok/s at 1/4/8 sessions: 50 / 80 / 72 (#608 build at 8: 72.1, 0 preemptions, TTFT p50 2.6 s, ITL p50 83 ms), against qwen 1.17.0's 50 / 96 / 99. At 12: 47-53 tok/s, TTFT p50 19-28 s, 11-15 preemptions. BetterBench's short prompts favour Inkling (275 vs 254 at 8); long sessions favoured qwen by ~27% on that build, superseded by the 2026-11-24 numbers below. Chosen for the faster single stream and prefill.
- Thinking cannot be turned off: `reasoning_effort: none`, top level or in `chat_template_kwargs`, still gives ~45 reasoning tokens before the answer; `enable_thinking: false` is ignored. From `docs/llm-hosting`, run `python3 bench/qualitygate.py needle --model inkling-2-mini --chat-needles` to use the chat template with a 1,024-token output budget. 8-ball escalation is disabled because a one-token request gets no answer.
- At the card's own 32K/4,096 conditions we measure 102 tok/s with the drafter and 64 without (card: 217 and 87). Our R9700 runs a 210 W cap (default 300 W), which the engine reports as the binding clock limit; the card's power setting is unknown.
- Raw logs: `~/.local/state/radiance-tune/{2026-11-23,next-inkling,gate-inkling}/` on the owner's laptop.

## Measurements (2026-11-24, same card, 210 W cap, same node for every pair)

prodshape at 8 sessions, aggregate tok/s: #608 build 72.8; replay fix and packed verify 75.0; compact verify workgroup 88.3; with the draft policy 94; with kernel variant 2 107.9 mean of 4 runs (best 111.5, ITL p50 33 ms, 0 preemptions). qwen 1.17.0: ~99. At 1 session all builds are 55-57.

Code and JSON generation (8 prompts, greedy, thinking off), aggregate tok/s, qwen vs this build: short prompts 1 session 113 vs 129, 8 sessions 297 vs 340; ~50K-token real-text prefixes 8 sessions ~203 vs 209 (run to run spread ~10%). The drafter pays on predictable text (+59% at 1 session, +14-21% at 8 against drafter off) and costs ~10% on synthetic filler.

Quality against the #608 build: drafter-off greedy output identical token for token on 60 samples (100%, and 100% between two #608 runs); GSM8K 146/150 (#608 145/150); tools 12/12 alone and 5 at once; vision 2/2; needles 9/9 plus cached prefix. KLD does not exercise the decode kernels (64-token prefill batches). Drafter-on greedy agreement between any two builds, or warm against cold on one build, is only 35-80%: speculative decoding changes tokens on long generations.

12 slots stay worse (49-52 tok/s, TTFT 27-35 s, 5-7 preemptions): the ~483K-token pool, not the engine, is the limit.

## Measurements (2026-11-25, compact-min, same card, 210 W cap)

`attnrel-d34cc357`, `RADIANCE_REL_VARIANT=2` in every arm; baseline u2 vs `RADIANCE_REL_COMPACT_MIN=1` arms c1 and c2 (u1 lost to a port clash).

| Workload | Baseline | Compact-min | Change |
| --- | --- | --- | --- |
| Code context, 1 session at 101K (run 1 / run 2) | 131.4 / 63.2 | 141.6 / 81.4 | +8% / +29% |
| Code context, 2 sessions at 30K | 156.9 / 128.7 | 172.3 / 140.6 | +10% / +9% |
| Code context, 4 sessions at 43K | 206.8 / 195.8 | 210.3 / 183.9 | neutral |
| prodshape aggregate, 1 / 2 / 4 sessions | 53.9 / 70.3 / 93.8 | 54.4 / 80.9 / 97.0 | +1% / +15% / +3% |

Gates with compact-min on: drafter-off agreement 100% (0/60, warm vs cold 20/20), GSM8K 146/150, tools 12/12 single and concurrent, vision 2/2, needles pass at 200K.

## qwen-3.8 on demand

radiance 2.x serves several models from one process (upstream `docs/MULTIMODEL.md`). `qwen-3.8` is the second entry of the fleet config; agentgateway sends it to the same Service (`agentgateway/app/models/qwen-3.8.yaml`). Started from JJGadgets/Biohazard's `qwen3.8-27b-radiance.yaml`.

- Weights: upstream's [StillDeadcode/qwen3.8-27b-iqr-v4](https://huggingface.co/StillDeadcode/qwen3.8-27b-iqr-v4) IQ4R-S-V4 container, prefetched by the `qwen38-27b` Model. It is base Qwen3.8-27B, not Swift 1.5 (which thinks less), with a DFlash2 drafter; KL 0.0064 against bf16. 2.x refuses Swift's M2XFP4 and block-FP8 containers and the 1.17 `.rad`, and the W4A16 checkpoint is gated.
- The two never fit on the card together. A request for the one not loaded takes the card once the other has served its quantum (5-30 s) and drained (30 s; open streams are then cut). The leaving model unloads (`park: ram: never`), so its sessions prefill again and the pod pins one model's host memory at a time. Under mixed traffic the two swap back and forth.
- Equal priority, no `ttl`: on this build every `/metrics` scrape counts as a use, so a model never goes idle (fixed in upstream 2.3.0, where a qwen `ttl` could hand the card back).
- `warm-up: false`: nothing loads at start. The pod is ready before Inkling is, and the first request waits for its load (up to `load-timeout`, 120 s).
- Untested on the card: qwen's first load on this build is the first time `qwen35_w8a8` runs here, and a device fault during it ends Inkling too. Run it in a bench window: the qualitygate modes with `--model qwen-3.8`, prodshape `--levels 1,4,6`, and the `kv:` line for the pool. Settings carried over from 1.17 (Swift, k6_v4) are not re-measured.
- Do not send `qwen-3.8` before the Model reads Ready: a missing file fails only qwen's load, but the attempt can still reclaim Inkling's free KV.

## Outside git

Clients configured on PVCs or in apps name the model themselves; switch them with the cutover:

- Hermes root and profiles, OpenCode: `qwen-3.8-auto` -> `inkling-2-mini-auto`.
- Home Assistant's assist agent: `qwen-3.8-fast` -> `inkling-2-mini-auto` (its API key may only call that name, `agentgateway/app/clients.yaml`). The `-auto` route selects the lowest supported effort for easy requests.

## Rollback

Serve `qwen-3.8` as the main model again: point the clients outside git back at it; the gateway already routes it. To drop it, remove its entry from `resources/radiance.yaml`, `agentgateway/app/models/qwen-3.8.yaml`, its two `modelcatalog.yaml` rates and the `AgentgatewayRequestsFailing` mute regex, then restart the pod (config edits do not roll it). The Swift 1.5 stack (W4A16 Model, rad-convert Job, `qwen38-27b-vllm` InferenceService) is gone: bringing it back means reverting that commit and porting its flags to 2.x keys.
