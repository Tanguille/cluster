# Inkling 2 Mini on radiance (2026-11)

`inkling-2-mini` replaced `qwen-3.8` as the local model on control-1's R9700 (gfx1201, 32 GB). The card holds one engine, so qwen's InferenceService stays in git with `suspend: true` as the rollback.

## Engine and model

- Image: `ghcr.io/tanguille/radiance:inkling2-perf608-<sha>`, our own build. Upstream support is radiance PR [#584](https://codeberg.org/StillDeadcode/radiance/pulls/584) (merged) and [#608](https://codeberg.org/StillDeadcode/radiance/pulls/608) (open; it replaced #585/#586). The build is the #608 branch plus two local changes:
  - host tests off: the branch's `r4d_emu` does not compile;
  - the `inkling-perf` patch: NVFP4 MoE takes the prefill tiles only from 16 routed rows an expert (`RADIANCE_NVFP4_PF_ROWS` overrides), and the sort writes `moe_gather_inv`'s slot table. +10% aggregate at 8 sessions in one run; upstream as [#613](https://codeberg.org/StillDeadcode/radiance/pulls/613), stacked on #608.
  - Built with rootless BuildKit from the upstream Dockerfile (ccache mount). Move to the upstream image once the PRs merge.
- Model: [gostar77/inkling2-mini-i8-dspark-radiance](https://huggingface.co/gostar77/inkling2-mini-i8-dspark-radiance), a ready `.rad` (int8 trunk, NVFP4 experts, DSpark drafter, vision tower). Its layout follows the engine build, so the revision is pinned and moves with the image.

## Config and why

| Flag | Value | Reason |
| --- | --- | --- |
| `--max-num-seqs` | 8 | ~483K-token pool; 12 production sessions (~624K) preempt (prodshape below) |
| `--kv-cache-dtype` | i8 | bf16 cannot hold one 262K sequence beside the experts; i8 passes the needles (below) |
| `--num-speculative-tokens` / `--draft-window` | 7 / adaptive | DSpark is 7 or off; fixed window lost 7-14% |
| `--reasoning-effort` | medium | default for requests without one; `inkling-2-mini-auto` picks per request |

## Measurements (2026-11-23, 210 W cap)

- KV pool: 501K tokens at 8 slots, 466K at 16 (the drafter's own KV takes 2.9 GiB).
- BetterBench 0.6.1 vs qwen 1.17.0: single stream 20-60% faster by category, cold prefill ~5,000 vs ~2,100 tok/s; aggregate C1/2/4/8 119/165/197/245 vs 89/161/218/254.
- Quality: GSM8K 143-147/150, tools 12/12, vision 2/2, needles 9/9 at 20K/60K/200K through `/v1/chat/completions`. Raw `/v1/completions` without the chat template invents the code: always serve through the chat API.
- prodshape (production-shaped agent sessions, ~52K-token prefixes), aggregate tok/s at 1/4/8 sessions: 50 / 80 / 72 (#608 build at 8: 72.1, 0 preemptions, TTFT p50 2.6 s, ITL p50 83 ms), against qwen 1.17.0's 50 / 96 / 99. At 12: 47-53 tok/s, TTFT p50 19-28 s, 11-15 preemptions. BetterBench's short prompts favour Inkling (275 vs 254 at 8); long sessions favour qwen by ~27%. Chosen anyway for the faster single stream and prefill.
- Thinking cannot be turned off: `reasoning_effort: none`, top level or in `chat_template_kwargs`, still gives ~45 reasoning tokens before the answer; `enable_thinking: false` is ignored. Quality harnesses leave it room (`qualitygate.py --chat-needles` uses 1,024 tokens), and a one-token request (eightball's logprob escalation) gets no answer.
- At the card's own 32K/4,096 conditions we measure 102 tok/s with the drafter and 64 without (card: 217 and 87). Our R9700 runs a 210 W cap (default 300 W), which the engine reports as the binding clock limit; the card's power setting is unknown.
- Raw logs: `~/.local/state/radiance-tune/{2026-11-23,next-inkling,gate-inkling}/` on the owner's laptop.

## Outside git

Clients configured on PVCs or in apps name the model themselves; switch them with the cutover:

- Hermes root and profiles, OpenCode: `qwen-3.8-auto` -> `inkling-2-mini-auto`.
- Home Assistant's assist agent: `qwen-3.8-fast` -> `inkling-2-mini-auto` (its API key may only call that name, `agentgateway/app/clients.yaml`). There is no `-fast` route: `-auto` turns thinking off for easy requests itself.

## Rollback

Revert the cutover commit: it brings back qwen's routes, names, alerts and UPS shed and drops `suspend` from `qwen38-27b-vllm.yaml`, whose `.rad` and model cache stayed on their PVCs. Then switch the clients outside git back.
