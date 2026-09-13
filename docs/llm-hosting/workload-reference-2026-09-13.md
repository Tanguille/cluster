# qwen38-27b-vllm production workload reference (2026-09-13)

The reference point for "busy and painful". Every optimisation is judged
against this window. Pod `qwen38-27b-vllm-6cff76c8bd-r5w97`, started
13:44:58Z after the lm_head fake-quant eval pod was reverted, observed
13:45Z to 15:13Z (Sunday afternoon, 15:45 to 17:13 CEST). Scripts:
`docs/llm-hosting/bench/prod_budget.py`, `prod_quantiles.py`, and the ad hoc
ones quoted inline, all against
`kubectl -n observability port-forward svc/vmsingle-victoria-metrics 18428:8428`.

## Serving stack (verified from the running ReplicaSet and boot log)

| | |
|---|---|
| GPU | one Radeon AI PRO R9700, gfx1201, 31.9 GiB VRAM, shared with Jellyfin/fileflows |
| Image | `vllm/vllm-openai-rocm:nightly@sha256:960228cf...` (2026-09-12), engine reports `v0.1.1.dev50+geed1f3d0c` |
| Model | Qwen3.8-27B W4A16 compressed-tensors, `lm_head` bf16, GDN hybrid (`Qwen3_5ForConditionalGeneration`, mamba cache mode `align`) |
| Args | `--max-model-len 246944 --kv-cache-dtype fp8_e4m3 --enable-prefix-caching --max-num-batched-tokens 4096 --gpu-memory-utilization 0.875 --max-num-seqs 5 --reasoning-parser qwen3 --tool-call-parser qwen3_coder` |
| Attention | AITER unified attention with the `attn_3d.num_stages=1` overlay; `GPU_MAX_HW_QUEUES=1` |
| KV | **fp8_e4m3 already** (not bf16). GPU pool 304,808 tokens = 1.23x one max-length request. Block = 800 tokens (mamba-page forced) |
| Offload | `OffloadingConnector` / `TieringOffloadingSpec`: CPU tier 23.56 GB tmpfs = 216 chunks of 4 blocks (3,200 tok), fs tier `/kvoffload` on local NVMe (51 GB, 500 files at observation), `kv_load_failure_policy=recompute` |
| Compile | AOT torch.compile cache hit, 1.21 s; weights load 38.9 s; pod Ready ~2.5 min after start |
| Node | control-1: 12 vCPU, 62.8 GiB RAM (8.9 GiB available during the window), load5 6.2 |

## Who sends the traffic

`litellm_requests_metric_total` over 80 min, model `qwen-3.8`: 222 requests.

| Client | Requests | Path | Notes |
|---|---:|---|---|
| Claude Code (`claude-cli/2.1.270`) via envoy-internal | 180 (81%) | `/v1/responses` | 164 as `qwen-3.8`, 16 as `qwen-3.8-fast` |
| Hermes (`OpenAI/Python 2.24.0`, pod 10.42.1.233) | 26 (12%) | `/v1/chat/completions` | 24 `qwen-3.8`, 2 `-fast` |
| curl probes/cron | 16 (7%) | | all `qwen-3.8-fast` |

Two litellm replicas (10.42.0.37 on control-2: 149 calls, 10.42.1.134 on
control-1: 126 calls) fan the same traffic onto the single vLLM pod.

Failures at litellm in the window: 12 x 400 `BadRequestError` on
`qwen-3.8-fast` (vLLM: "Chat template rejected the request: System message
must be at the beginning", the known Claude Code Environment-after-first-
message pattern, 24 rejected `/v1/responses` at vLLM) and 8 x
`ProxyModelNotFoundError` for `claude-opus-4-7` / `claude-sonnet-4-6` (a
client asking litellm for models it does not route).

Token mix (litellm, 80 min): input 9,471,259, output 135,926, of which
**reasoning 74,670 (55% of output)**.

## Request shape (vLLM histograms, `prod_hist.py 80m`, n=244)

Prompt tokens:

| bucket | share | cum |
|---|---:|---:|
| <= 5K | 5.3% | 5.3% |
| 5K to 20K | 14.4% | 19.7% |
| **20K to 50K** | **51.6%** | 71.3% |
| 50K to 100K | 25.8% | 97.1% |
| 100K to 200K | 2.9% | 100% |

Mean prompt 45,427; mean cached 39,002 (85.9%); mean computed 6,084.

Prefill tokens actually computed per request (the cache-miss size):

| bucket | share | cum |
|---|---:|---:|
| <= 1K | 38.1% | 38.1% |
| 1K to 5K | 35.3% | 73.4% |
| 5K to 20K | 16.8% | 90.2% |
| **20K to 100K** | **9.8%** | 100% |

The 24 requests over 20K computed tokens (~10% of requests) hold ~60% of
all prefill compute in the window (22 x ~35K + 2 x ~75K of a 1.48M total).

Generation tokens: median in the 200 to 500 bucket, 73% <= 500 (agent
turns), 8.6% >= 2,000, 4.9% >= 5,000. The 12 requests over 5K tokens are
~40% of all generated tokens. `max_tokens` histogram is identical to the
generation histogram, so nothing is cut off by the client cap.

Cache: GPU prefix hit 44.7% (queries), external (offload) prefix hit 74.4%,
fs-tier chunk hit 51.0%. Combined 85.9% of prompt tokens served from some
cache.

## Load and latency (75 min, 221 completed, `prod_budget.py 75m`)

Rate 2.95 req/min (194/h at the end of the window; 7-day mean 66/h, so
this is ~3x the average hour). `num_requests_running` avg 3.8, max 5
(= max-num-seqs, saturated), waiting avg 1.55, peak waiting 8, deferred
(waiting on KV load) avg 0.70. GPU KV usage avg 73%, peak 98%.

| Per request | mean | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| queue | 28.8 s | 10.2 | 100.5 | 218.7 |
| prefill | 7.0 s | 2.3 | 17.3 | 49.2 |
| TTFT | 37.0 s | 19.5 | 112.3 | 414.4 |
| decode | 63.8 s | 17.8 | 162.5 | 821.4 |
| e2e | 100.4 s | 50.3 | 224.1 | 881.4 |

TPOT 72.8 ms mean (~14 tok/s per stream at ~4 streams). litellm's own view
agrees: TTFT mean 66 s, total latency mean 90 s.

## Where the GPU time goes (engine 10 s stat lines, `prod_engstat.py`, 480 windows)

| | |
|---|---|
| Aggregate decode when not stalled | mean 53.9 tok/s, median 52.8, max 92 |
| **Stalled windows** (running >= 4, generation < 5 tok/s, no prompt tokens booked) | **89 of 480 = 19% of wall time, 890 s** |
| Stall runs | 34 runs, median 30 s, longest 70 s |
| Prompt throughput when booked | mean 876 tok/s, median 306, max 6,758 (booked in bursts when a prefill completes) |

What a stall looks like (15:06:05Z to 15:07:05Z): TG 0.8 to 1.2 tok/s with 5
running, PP 0.0, GPU KV usage climbing 78 -> 92% at ~3 points per 10 s,
stores of 2 to 3 chunks per 10 s, no loads. That is one large uncached
prompt being chunk-prefilled at 4,096 tokens per step; each step costs
~3 s of GPU, so the four co-resident decode streams get one token per
~3 s. vLLM books the prompt tokens only when the prefill finishes, which
is the 6,758 tok/s spike at 15:08:35Z. **A cache-missed 45K prompt is a
40 s freeze for everyone.**

Second stall source: `kv_offload_lookup_async_delay_seconds` 108 events,
mean 13.1 s, total 1,414 s (6.4 s per request amortised); tiering lookup
352 events, total 743 s. Histogram buckets cap at 10 s. Data path is not
the cost: loads 84, 0.19 s/request at 3.2 GiB/s, stores 0.02 s/request.

## GPU physical state (node exporter, `192.168.0.11:9100`, card1, 80 min)

| | |
|---|---|
| `node_hwmon_power_average_watt` | avg **247.7 W against a 250 W cap**, peak 307 |
| `node_hwmon_temp_celsius` max | temp1 (edge) 78, temp2 (junction) **95**, temp3 (mem) **94** |
| sclk during the window (sysfs samples at run=5) | 2,470 to 2,827 MHz, never the 3,100+ seen in single-stream decode |
| VRAM used | 29.4 of 31.9 GiB, flat |
| `gpu_busy_percent` | 100 (useless, as always) |

The card is power- and thermal-limited under this load. Band: the probe's
`/tmp/band` held `1 1` (two fast samples, both at the rare run=1 moments);
consistent with fast, not formally classified in this window.

## What "painful" means, in numbers

- Median request waits 10 s before anything happens; one in ten waits over
  100 s.
- One in ten requests is a cache miss over 20K tokens and freezes the other
  four streams for 30 to 70 s.
- 19% of wall time the engine emits ~1 tok/s total.
- When it is not stalled, aggregate decode is ~54 tok/s split five ways.
- 55% of the tokens generated are reasoning tokens the user never reads.

## Reproduce

```sh
kubectl -n observability port-forward svc/vmsingle-victoria-metrics 18428:8428 &
python3 docs/llm-hosting/bench/prod_budget.py 75m
python3 docs/llm-hosting/bench/prod_quantiles.py 75m
python3 docs/llm-hosting/bench/prod_hist.py 80m
python3 docs/llm-hosting/bench/prod_clients.py 80m
kubectl -n ai logs <pod> -c vllm --since=80m | grep 'Engine 000' > engine.log
python3 docs/llm-hosting/bench/prod_engstat.py engine.log
```
