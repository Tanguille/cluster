# Reranker: CPU vs iGPU (Vulkan)

Decision record: `bge-reranker-v2-m3` stays on the iGPU. CPU is 2.5-3.2x slower and
breaks memini's 8s rerank timeout. Sibling of [embedder-cpu-vs-igpu.md](embedder-cpu-vs-igpu.md).

## Method (2026-10-06, control-3)

Same llama.cpp build (11429, `d81235049`), same model file, same args; only the backend differs.

- **Model**: `bge-reranker-v2-m3` Q8_0, mounted read-only from the live `llmkube-model-cache` PVC (RWO, same node).
- **Args** (both): `--ctx-size 4096 --parallel 1 --batch-size 4096 --ubatch-size 4096 --reranking --embedding --pooling rank --cache-ram 0`.
  - iGPU: the live `bge-reranker-v2-m3` InferenceService (`--n-gpu-layers 99`).
  - CPU: temporary pod, same `server-vulkan` image (it ships the `libggml-cpu-*` backends), `--device none --n-gpu-layers 0`, 6 threads (llama.cpp default), no `squat.ai/dri` slot, `requests.cpu: 1`. Confirmed CPU-only: `kubectl logs` says "no usable GPU found" and the pod has no `/dev/dri`.
- **Driver**: [bench/rerank_cpu_vs_igpu.py](bench/rerank_cpu_vs_igpu.py) via `kubectl port-forward -n ai deploy/bge-reranker-v2-m3 18081:8080` and `... pod/rerank-cpu-test 18082:8080`, 1 warmup discarded, 15 requests per backend, iGPU and CPU interleaved so drift and live memini traffic hit both. Each request scores 12 documents (memini's `MEMINI_RERANK_POOL`) cut from repo `docs/**/*.md` (main checkout at `25ee4d5b7`, untracked files included), so scores are not reproducible; latency is, since it scales with chars.
- **Load**: control-3 was at ~48% CPU (`kubectl top nodes`) with other workloads, so CPU numbers include realistic contention.

## Results (seconds per 12-doc request)

| docs | iGPU p50 | iGPU max | CPU p50 | CPU max | CPU slowdown |
|---|---|---|---|---|---|
| 12 x 1024 chars (`MEMINI_RERANK_MAX_DOC_CHARS`) | 3.39 | 3.54 | 8.41 | 10.91 | 2.5x |
| 12 x 300 chars | 1.16 | 1.20 | 3.70 | 3.74 | 3.2x |

Score parity: max abs diff 0.039 (1024) and 0.020 (300). Rankings match except near-ties ~0.003 apart, so Q8_0 scores the same on both backends.

## Decision

Keep the iGPU. CPU at 1024 chars (8.4s p50) exceeds `MEMINI_RERANK_TIMEOUT: 8s`, so nearly every recall would time out. Not tested: more than 6 threads.

## Open question

Raw scores were about -9 to -11 for unrelated docs, but `MEMINI_RERANK_MIN_SCORE: "0.5"` assumes [0,1]. Not investigated.
