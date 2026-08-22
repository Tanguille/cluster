# Runbook: vLLM admission stall after a restart

## Symptom

Inference is slow to unusable. The pod is `1/1 Running`, `/health` returns 200,
and `InferenceServiceDown` never fires. `LiteLLMPrimaryDegraded` may fire, which
only says fallbacks are happening.

Engine log shows queued requests with no prefill progress:

```
Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 11.1 tokens/s,
Running: 1 reqs, Waiting: 5 reqs, GPU KV cache usage: 63.5%
```

Queued requests with a third of the KV cache free is the tell.

## Confirm

```bash
P=$(kubectl get pods -n ai --no-headers | grep qwen38-27b-vllm | grep -v guard | awk '{print $1}')
kubectl exec -n ai "$P" -c vllm -- curl -s localhost:8000/metrics | grep -v '^#' \
  | grep -E "kv_offload_lookup_async_delay_seconds_(sum|count)|tiering_lookup_async_delay_seconds_(sum|count)"
```

Divide sum by count. Healthy is <5s. During the 2026-08-20 incident the
connector-level mean was **28.66s** while the fs tier's own lookup was 1.67s --
that gap is the signature.

## Cause

A restart wipes the GPU prefix cache (measured 95% -> 8%). With it cold, nearly
every request takes the `load_kv_async` path. Each in-flight async load reserves
its full remaining blocks and is not preemptible, and
`scheduler_reserve_full_isl` (default true) then requires the whole input
sequence to fit in what is left. `allocate_slots` returns None and the waiting
loop hits a hard `break` -- hence prompt throughput of exactly 0.0 rather than
graceful degradation.

It is self-sustaining: the cache cannot warm because requests cannot be admitted.
Recovery is load-dependent, not time-dependent. On 2026-08-20 it degraded for six
hours (0.08s -> 73s mean load delay) with no sign of recovering.

Source: `v1/core/sched/scheduler.py:1026-1044`, `config/scheduler.py:130`.

## Fix

Clear the fs offload tier. The arithmetic that justifies it: a tier **hit** costs
~28s of blocked admission, a **miss** costs a fresh prefill at ~19.8s mean. The
cache is net-negative in this state, so discarding it is a win, not a loss.

```bash
flux suspend kustomization llmkube-models -n ai
kubectl patch inferenceservice qwen38-27b-vllm -n ai --type merge -p '{"spec":{"replicas":0}}'
# wait for the pod to release the PVC, then clear it with a throwaway pod
# mounting qwen38-27b-vllm-kv-offload (RWO, openebs-hostpath, pinned to control-1):
#   find /kvoffload -mindepth 1 -delete
kubectl patch inferenceservice qwen38-27b-vllm -n ai --type merge -p '{"spec":{"replicas":1}}'
flux resume kustomization llmkube-models -n ai
```

~4 min to serve again (63s of that is weights). Verify with a real request
through litellm, not just pod readiness. 2026-08-20: 279.9 GB cleared, volume 82%
-> 26%, first probe 200 in 2s against 28-73s stalls.

## Ruled out — do not re-investigate

Each of these was measured during the incident and was NOT the cause:

- **Disk I/O.** A 25 MB offload chunk reads in 20 ms (~1.25 GB/s); bulk transfers
  measure 3.39 GB/s. No node disk pressure.
- **O_DIRECT fallback.** No "falling back to buffered I/O" warning was logged.
- **fs thread pool.** Already defaults to 16 read / 16 write threads.
- **maxModelLen / "Maximum concurrency 1.17x".** That line is `logger.info_once`,
  informational only; `_request_remaining_blocks` uses
  `min(request.num_tokens, max_model_len)`, so the request's own size binds.
- **DP prefill throttling.** Base `_should_throttle_prefills` returns False;
  single engine here.
- **`_reserve_prefill_lookahead`.** No-op without MTP.

## Prevention

`VLLMQueueTimeHigh` and `VLLMKVOffloadLoadSlow`
(`kubernetes/apps/ai/llmkube/models/prometheusrule.yaml`) backtest to firing
3h15m and 2h15m before this was noticed by hand.

The durable rule is operational: **do not restart this engine under load.** The
cost is not the ~4 min boot, it is hours of degraded admission afterwards. Batch
config changes, and prefer changes that apply without a restart. Note also that
the pod cannot reschedule -- it needs control-1's dGPU and two node-local
`openebs-hostpath` PVCs -- so any control-1 outage produces this same cold-start
condition on return.
