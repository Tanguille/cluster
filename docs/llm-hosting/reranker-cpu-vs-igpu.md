# Reranker: CPU vs iGPU (Vulkan)

Decision record: `bge-reranker-v2-m3` stayed on the iGPU (CPU is 2.5-3.2x slower and
breaks memini's rerank timeout at 12 docs per request); since 2026-11-15 it runs on the RTX 2070
(see the end). Sibling of [embedder-cpu-vs-igpu.md](embedder-cpu-vs-igpu.md).

## Method (2026-10-06, control-3)

Same llama.cpp build (11429, `d81235049`), same model file, same args; only the backend differs.

- **Model**: `bge-reranker-v2-m3` Q8_0, mounted read-only from the live `llmkube-model-cache` PVC (RWO, same node).
- **Args** (both): `--ctx-size 4096 --parallel 1 --batch-size 4096 --ubatch-size 4096 --reranking --embedding --pooling rank --cache-ram 0` (the live spec is now 2048, same scores, far less GTT).
  - iGPU: the `bge-reranker-v2-m3` InferenceService as of 2026-10-06 (`--n-gpu-layers 99`).
  - CPU: temporary pod, same `server-vulkan` image (it ships the `libggml-cpu-*` backends), `--device none --n-gpu-layers 0`, 6 threads (llama.cpp default), no `squat.ai/dri` slot, `requests.cpu: 1`. Confirmed CPU-only: `kubectl logs` says "no usable GPU found" and the pod has no `/dev/dri`.
- **Driver**: `rerank_cpu_vs_igpu.py` (not committed) via `kubectl port-forward -n ai deploy/bge-reranker-v2-m3 18081:8080` and `... pod/rerank-cpu-test 18082:8080`, 1 warmup discarded, 15 requests per backend, iGPU and CPU interleaved so drift and live memini traffic hit both. Each request scores 12 documents (memini's `MEMINI_RERANK_POOL` then, 4 since 3c120e259) cut from repo `docs/**/*.md` (main checkout at `25ee4d5b7`, untracked files included), so scores are not reproducible; latency is, since it scales with chars.
- **Load**: control-3 was at ~48% CPU (`kubectl top nodes`) with other workloads, so CPU numbers include realistic contention.

## Results (seconds per 12-doc request)

| docs | iGPU p50 | iGPU max | CPU p50 | CPU max | CPU slowdown |
|---|---|---|---|---|---|
| 12 x 1024 chars (`MEMINI_RERANK_MAX_DOC_CHARS`) | 3.39 | 3.54 | 8.41 | 10.91 | 2.5x |
| 12 x 300 chars | 1.16 | 1.20 | 3.70 | 3.74 | 3.2x |

Score parity: max abs diff 0.039 (1024) and 0.020 (300). Rankings match except near-ties ~0.003 apart, so Q8_0 scores the same on both backends.

## Decision

Keep the iGPU. CPU at 12 x 1024 chars (8.4s p50) exceeds `MEMINI_RERANK_TIMEOUT` (8s then, 5s since 8429c1d8a), so nearly every 12-doc recall would time out. memini's pool is 4 since 3c120e259; 4 docs per request and more than 6 threads are untested.

Resolved: scores are raw logits; the MIN_SCORE gate was dropped in 8429c1d8a.

## RTX 2070 and candidate rerankers, 2026-11-15

Throwaway pod on the 2070, llama.cpp b12407 `server-cuda`, ctx 2048, `-fa on`, serial calls of
1024-char docs built from the codingagent_v1 items. Recall: memini `cmd/bench` at v0.7.39 on
codingagent_v1 (166 questions), embeddinggemma-2 on the same card, `-rerank-max-doc-chars 1024`
(`bench -suite file -data bench/data/codingagent_v1.json -k 3 -rerank-url ... -llm-rerank-pool N`).

| Reranker | VRAM MiB | p95 10 docs | p95 15 docs | R@1 / R@3 pool 4 | R@1 / R@3 pool 12 |
| --- | --- | --- | --- | --- | --- |
| bge-reranker-v2-m3 Q8_0 | 510 | 0.30 s | 0.44 s | 65.1% / 88.0% | 64.5% / 88.0% |
| Qwen3-Reranker-0.6B Q8_0 (ggml-org) | 1060 | 0.31 s | 0.46 s | 59.6% / 87.3% | 59.6% / 86.7% |
| gte-reranker-modernbert-base Q8_0 (jolleyboy) | 290 | 0.13 s | 0.19 s | 60.8% / 88.0% | 60.8% / 87.3% |

Unranked fused order: 58.4% / 86.1%. bge reproduces its 2026-10-15 iGPU numbers exactly. Neither
candidate beats bge (the switch needed R@3 at least 1 point higher), so bge stays: Qwen3-Reranker's
published code-retrieval lead does not show on this set. On the 2070 bge needs 0.39 s for 15 docs
(0.97 s p95 beside a concurrent embedder load) against ~5 s for 12 on the iGPU.
