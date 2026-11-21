# BetterBench

[BetterBench](https://github.com/GGZ14/BetterBench) (0.6.1, corpus v1.0) is a percentile benchmark for OpenAI-compatible endpoints: single-stream decode, prefill by depth and a concurrency sweep.

## Radiance reference run, 2026-11-21

[radiance-quick-2026-11-21.html](radiance-quick-2026-11-21.html) is a self-contained report (open it in a browser). Radiance 1.17.0, MTP, k6_v4 KV, 8 slots.

| Sessions | Aggregate tok/s | TTFT p50 |
| --- | --- | --- |
| 1 | 53.3 | 160 ms |
| 2 | 131.2 | 161 ms |
| 4 | 136.6 | 201 ms |
| 8 | 112.5 | 5.3 s |

Single-stream combined decode 77.7 tok/s, prefill 2,126 tok/s at 1.5K tokens falling to 1,631 at 47K.

Caveats:

- `--quick` (5 passes), against the live pod with production traffic alongside: the pod decoded 177,751 tokens in the 30 min run (`radiance:decode_tokens_total`), of which the bench generated about 98,000, so roughly 45% came from other clients. Indicative only; p99 values rest on too few samples.
- 34 of 40 single-stream runs hit `max_tokens` while still thinking, so decode measures the thinking phase.
- The report's `gpu` line is the client laptop (gfx1150), not the R9700.
- Not comparable with the [runbook](../llm-hosting/radiance-2026-11.md) tables: different tool, prompts and sampling (temp 0.7).

## Re-running

```bash
kubectl -n ai port-forward svc/qwen38-27b-vllm 18000:8000
betterbench run --endpoint http://localhost:18000/v1 --model qwen-3.8 --quick --name radiance-live \
  --note engine=radiance --note kv=k6_v4 --note spec=mtp --note slots=8
```

Pause production for a publishable run (drop `--quick`, 20 passes, hours).
