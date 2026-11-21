# BetterBench

[BetterBench](https://github.com/GGZ14/BetterBench) (0.6.1, corpus v1.0) is a percentile benchmark for OpenAI-compatible endpoints: single-stream decode, prefill by depth and a concurrency sweep. Reports are self-contained HTML, open them in a browser.

## Radiance reference, 2026-11-21

Radiance 1.17.0, MTP, k6_v4 KV, 8 slots, 20 passes per category, temp 0.7. Client namespaces were blocked from the pod for the runs (temporary CiliumNetworkPolicy, since deleted), so no production traffic shared the GPU. A UPS load-shed stopped the first run partway, so the result is three files:

| Report | Holds |
| --- | --- |
| [1-single-stream](radiance-2026-11-21-1-single-stream.html) | chat, code, file_edit, json, math, prose. Its reasoning, summarization, concurrency and prefill rows are empty (pod stopped) |
| [2-reasoning-summarization](radiance-2026-11-21-2-reasoning-summarization.html) | reasoning, summarization |
| [3-concurrency-prefill](radiance-2026-11-21-3-concurrency-prefill.html) | concurrency sweep, prefill sweep |

Single-stream decode (tok/s, median, TTFT p50 about 105 ms):

| chat | code | file_edit | json | math | prose | reasoning | summarization |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 68.6 | 102.2 | 130.5 | 128.0 | 116.2 | 65.3 | 73.8 | 101.5 |

Concurrency and prefill:

| Sessions | Aggregate tok/s | TTFT p50 | TTFT p99 |
| --- | --- | --- | --- |
| 1 | 88.7 | 103 ms | 157 ms |
| 2 | 160.7 | 155 ms | 218 ms |
| 4 | 217.8 | 176 ms | 302 ms |
| 8 | 254.4 | 189 ms | 428 ms |

| Prompt tokens | 1,507 | 5,902 | 11,778 | 23,487 | 47,069 |
| --- | --- | --- | --- | --- | --- |
| Prefill tok/s | 2,345 | 2,207 | 2,166 | 2,060 | 1,887 |

Caveats:

- Many thinking runs hit `max_tokens` (for example 9 of 20 reasoning runs never reached an answer), so decode measures the thinking phase.
- The reports' `gpu` line is the client laptop, not the R9700.
- Not comparable with the [runbook](../llm-hosting/radiance-2026-11.md) tables: different tool, prompts and sampling.
- An earlier 5-pass run with production traffic alongside (about 45% of the pod's decode tokens) read 112 tok/s at 8 sessions with TTFT p50 5.3 s; it is not kept.
- UPS load rose from about 54% idle to 92% under the bench (gaming on the same UPS pushed an earlier attempt to 100% and tripped the `qwen38-27b-vllm-ups-shed` rule), so run it with the UPS in mind.

## Re-running

```bash
kubectl -n ai port-forward svc/qwen38-27b-vllm 18000:8000
betterbench run --endpoint http://localhost:18000/v1 --model qwen-3.8 --name radiance-full \
  --note engine=radiance --note kv=k6_v4 --note spec=mtp --note slots=8
```

The `radiance:decode_tokens_total` counter shows how much of the pod's decode came from other clients during a run.
