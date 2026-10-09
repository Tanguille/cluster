# Honest Memory Accounting for control-2/3 Implementation Plan

**Created:** 2026-10-09
**Status:** In Review (CRIT-1 needs an owner decision, see Prerequisites)
**Complexity:** High
**Estimated Chunks:** 6
**Branch:** `feat/ram-honest-requests`
**Worktree:** `.claude/worktrees/ram-optimization`

---

## Overview

control-2 and control-3 (29.1 GiB, 25.6 GiB allocatable) hit <1 GiB MemAvailable almost daily, and control-2 thrashed on 2026-10-08/09 (0.35 GiB available, 19k major faults/s, controllers crash-looping). The scheduler and kubelet act on wrong numbers: iGPU GTT is invisible to cgroups, and several pods request far less than they use. This plan makes GTT visible per pod in Prometheus, bounds it, and makes memory requests honest from 30-day history, without adding placement rules.

---

## Baseline (measured 2026-10-09 ~00:15Z via tanguille-site, Grafana uid `prometheus`)

| Node | MemTotal | Allocatable | Requests | Working set | MemAvailable now | 14d daily min avail |
|---|---|---|---|---|---|---|
| control-1 | 62.8 | 59.3 | 36.3 | 27.9 | 32.0 | 12.2 to 32.9 |
| control-2 | 29.1 | 25.6 | 23.9 | 17.0 | 0.35 (5.5 after pod deletes) | 0.22 to 9.0, last 3 days <1.4 |
| control-3 | 29.1 | 25.6 | 24.4 | 23.4 | 2.0 (0.67 after reranker moved in) | 0.22 to 8.4, <1 on 11 of 14 days |

GTT (`node_drm_memory_gtt_used_bytes`): 1.8 GiB baseline per iGPU node, 3.5 to 7.7 GiB daily max.

Findings:
1. **Kubelet eviction is blind to GTT.** `evictionHard memory.available: 1Gi` (talos/cluster.yaml.j2:28) never fired at 0.35 GiB MemAvailable.
2. **GTT is not charged to any memcg on the running kernel.** Kernel 7.2.9-talos exposes node totals (`GPUActive` in /proc/meminfo), but the reranker pod's `memory.stat` (kubepods/burstable/pod3b68bb5a…) has no GPU key and `memory.current` is 167 MB while the node holds 2.4 GB GTT. Upstream memcg charging for TTM/amdgpu (Airlie's series) is not merged; AMD committed to it on 2026-09-18 without a timeline. Kubernetes cannot see GTT natively until that lands.
3. **The live reranker runs a stale config.** Flux Kustomization `ai/llmkube-models` is suspended (last applied `35a27a447`, 2026-10-07). Git (4445ee91f) has `contextSize/uBatchSize 2048` and `memory 1536Mi`; live has `4096/4096` and `512Mi`, which the manifest comment says "held 4.4 GiB of GTT". The 2026-10-08 control-2 GTT climb (1.78 → 3.57 → 6.57 → 7.72 GiB; dropped to 2.60 GB only when the reranker pod was deleted) was measured on the 4096 config.
4. **Reranker placement is a coin flip.** Its two preferred anti-affinity terms (embedder, qwen35-2b) both weigh 100 (bge-reranker-v2-m3.yaml:83-88 for the qwen35-2b term).
5. **qwen3-embedding requests exclude GTT on purpose** (512Mi request, ~1.9 GiB GTT; qwen3-embedding.yaml:99-103, an earlier owner decision). The git reranker (1536Mi) and qwen35-2b (1Gi) already include their GTT.
6. **Under-requests** (30d p99 working set minus request; to be redone per workload with RSS in Chunk 1): kube-apiserver p99 5.0 to 6.5 GiB per node vs the 512Mi Talos default, dragonfly p99 1.68 GiB vs 128Mi, postgres p99 2.69 GiB vs 2Gi, plus several movable apps. Working set includes active page cache, so page-cache-heavy pods need RSS-based sizing.
7. **The descheduler cannot help today.** Only `RemoveFailedPods`, `RemovePodsViolating*` and the topology-spread balance plugin are enabled (kube-system/descheduler/app/helmrelease.yaml:44-51). `LowNodeUtilization` is off, and if it were on it would balance on requests or metrics-server usage, neither of which includes GTT; evicted pods would land back by the same wrong requests.
8. **kubeReserved is under-sized on purpose** (2Gi vs /podruntime peaks up to 14.1 GB on control-3, talos/cluster.yaml.j2:36-41). Honest pod requests cannot fix that part; it is a known risk for US-1.

---

## User Stories

### US-1: Stop iGPU node starvation
**As** the cluster owner **I want** control-2/3 to keep at least 2 GiB MemAvailable **so that** kubelet, etcd and controllers never starve.

#### Acceptance Criteria
- [ ] 7 consecutive days with `min_over_time(node_memory_MemAvailable_bytes{nodename=~"control-[23]"}[1d]) > 2 GiB`
- [ ] `NodeMemoryAvailableLow` does not fire in that window
- [ ] No regression: 15 × `/v1/rerank` with 12 × 1024-char docs via port-forward gives p50 ≤ 3.42 s; 0 Pending pods for 10 min after every reconcile

### US-2: GTT is visible per pod
**As** the cluster owner **I want** a per-pod GTT metric **so that** Grafana and alerts name the leaking pod instead of the node.

#### Acceptance Criteria
- [ ] A Prometheus series with `namespace`, `pod` labels whose sum per node matches `node_drm_memory_gtt_used_bytes` within 10%
- [ ] `NodeGTTMemoryHigh` description names the top pod

### US-3: Requests match reality
**As** the cluster owner **I want** each changed request to equal the 30d p99 (RSS-based where working set is mostly cache, plus GTT where relevant), rounded up to a power of two **so that** the scheduler's view matches the node.

#### Acceptance Criteria
- [ ] Every changed value cites its query and result in the commit or PR body
- [ ] After each wave, on every node: `allocatable − Σrequests(Running+Pending) ≥ 1 GiB` and 0 Pending pods for 10 min

---

## Technical Analysis

### Modified/New Files
| File | Changes | Chunk |
|------|---------|-------|
| `kubernetes/apps/ai/llmkube/models/bge-reranker-v2-m3.yaml` | drop one anti-affinity term (chosen from data); memory to 2Gi (power of two) | 2 |
| `kubernetes/apps/ai/llmkube/models/qwen3-embedding.yaml` | memory 512Mi → RSS + GTT rounded (expect 2Gi), only with owner OK (reverses the comment at :99-103) | 2 |
| per-pod GTT exporter (location decided in Chunk 1: extend an existing exporter if one supports DRM fdinfo, otherwise a small DaemonSet under `kubernetes/apps/observability/exporters/`) | per-pod `drm-memory-gtt` from `/proc/<pid>/fdinfo` | 3 |
| `kubernetes/apps/observability/exporters/node-exporter/app/prometheusrule.yaml` | `NodeGTTMemoryHigh` names the top pod | 3 |
| movable workload HelmReleases listed by Chunk 1 | memory requests to 30d p99 | 4 |
| `kubernetes/apps/database/dragonfly/cluster/cluster.yaml` | requests.memory 128Mi → 2Gi (= limit; `--maxmemory` derives from the limit, unchanged) | 5 |
| `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` | requests.memory 2Gi → 4Gi (= limit), comment at :169 updated | 5 |

### Conventions Checklist
- [ ] Power-of-two memory values
- [ ] Only workloads with an explicit resources block (.agents/learned-preferences.md:44); kopiur mover has none (components/kopiur/backup/snapshotpolicy.yaml) → SKIP
- [ ] No new nodeAffinity/nodeSelector toward control-1 (owner, 2026-10-09)
- [ ] `mise exec -- flate test all` (baseline 308/308) and `bash .agents/skills/pr-review/scripts/validate-pr.sh` before every commit
- [ ] `flate diff all --base origin/main -o diff` roll list equals the chunk's intended list, AND live spec (`kubectl get <kind> -o yaml`) compared to git for every touched object (live drift exists, see Finding 3)
- [ ] For llmkube changes: `kubectl -n ai get ks llmkube-models -o jsonpath='{.spec.suspend} {.status.lastAppliedRevision}'` shows `false` and the merge sha before claiming anything is live
- [ ] Conventional commit titles, no Claude attribution; push, reconcile and Talos applies only with explicit owner OK; GitHub writes per .agents/learned-preferences.md:28-31

---

## Decisions & Trade-offs

### Decision 1: Honest requests, no placement rules (owner, 2026-10-09)
**Consequence:** when a request grows, the next restart lets the scheduler place that pod wherever it fits. Some movable pods will land on control-1 by fit.

### Decision 2: GTT visibility
**Native (kubelet/scheduler see GTT):** not possible on kernel 7.2.9; needs the upstream memcg TTM/amdgpu charging (not merged). Revisit when it lands; then GTT counts toward the pod's memory and its limit automatically.
**Chosen now:** per-pod GTT metric in Prometheus (Chunk 3) for visibility, plus GTT folded into iGPU pod requests (Chunk 2) so the scheduler accounts for it.
**Open option for the owner:** a hard per-node GTT cap via kernel arg `ttm.pages_limit` (exists on the nodes: /sys/module/ttm/parameters/pages_limit). Turns a node-wide starvation into an allocation failure in the leaking pod. Costs a schematic change and a reboot per iGPU node, and risks model load failures if set too low. Not in this plan unless chosen.

### Decision 3: Stateful rolls (owner approved)
One controlled roll each for dragonfly and postgres16, only after a per-node room pre-flight. postgres16 instances sit on openebs-hostpath local PVs (postgres16-8/1/4 on control-1/2/3), so an instance can only restart on its own node; dragonfly uses hostname spread with `DoNotSchedule`.

### Decision 4: kube-apiserver request (deferred, owner to pick)
30d p99 is 5.0 to 6.5 GiB per node; power-of-two gives 8Gi, +7.5 GiB per node over the 512Mi default. That does not fit on control-2/3 without moving about 7.5 GiB of pods per node to control-1, and a critical static pod over-requesting can make the kubelet evict pods to admit itself. Options: (a) leave at the default and treat it as part of the known kubeReserved-style gap, (b) a per-node sub-p99 value in `talos/nodes/controlplane/*.yaml.j2`, (c) 8Gi after Chunk 4 proves enough room. **Default if no answer: (a).**

### Decision 5: Descheduler
Re-evaluated in Chunk 6, after requests are honest.

### Out of scope (owner)
Hardware RAM upgrade; soft-preferring control-1; CPU embedders/reranker; a second reranker replica.

---

## Dependencies

### Prerequisites
- [x] Immediate relief done 2026-10-09 ~00:20Z: deleted qwen3-embedding and bge-reranker pods on control-2 (control-2 MemAvailable 0.4 → 5.5 GB; reranker now on control-3).
- [ ] **CRIT-1, owner:** why is `ai/llmkube-models` suspended? Resuming applies the reranker 2048 fix, but also the vLLM 27B image bump (a56dbe886) and would overwrite the live-only UltraQuant state that PR #5635 syncs. Proposed: merge #5635 first, then `flux resume ks llmkube-models -n ai`. Chunk 2 cannot go live before this.
- [ ] Open PR #3449 (DRA) edits the llmkube model files: rebase whichever lands second.

---

## Execution Plan

### Progress Tracker

#### Wave 1 (sequential) — Measure
- [ ] Chunk 1: sizing table, per-process GTT, apiserver/podruntime (read-only)

#### Wave 2 (parallel) — iGPU accounting and visibility
- [ ] Chunk 2: iGPU models (after CRIT-1 resolved)
- [ ] Chunk 3: per-pod GTT metric + alert text

#### Wave 3 (sequential) — Movable workloads
- [ ] Chunk 4: honest requests on movable workloads (depends on Chunk 2 being live)

#### Wave 4 (sequential) — Stateful
- [ ] Chunk 5: dragonfly + postgres16 (depends on Chunk 4)

#### Wave 5 (sequential) — Verify
- [ ] Chunk 6: 7-day verification, descheduler and apiserver decisions, docs

### Wave Conflict Matrix
| Chunk | Write Set | Dependencies | Wave |
|-------|-----------|--------------|------|
| 1 | this plan file (appendix) | None | 1 |
| 2 | `llmkube/models/bge-reranker-v2-m3.yaml`, `llmkube/models/qwen3-embedding.yaml` | Chunk 1, CRIT-1 | 2 |
| 3 | new exporter files under `observability/exporters/`, `node-exporter/app/prometheusrule.yaml` | Chunk 1 | 2 |
| 4 | movable HelmReleases from the appendix (no llmkube, dragonfly, cnpg, Talos, observability exporter files) | Chunk 2 live | 3 |
| 5 | `database/dragonfly/cluster/cluster.yaml`, `database/cloudnative-pg/cluster/cluster.yaml` | Chunk 4 | 4 |
| 6 | docs target, descheduler HelmRelease only if chosen | Chunk 5 | 5 |

Chunks 2 and 3 share no file and compete for no node room (Chunk 3 is a small DaemonSet; size it from the same pre-flight).

---

### Chunk 1: Measure and size (read-only)
**Status:** Not Started
**Wave:** 1
**Estimated Context:** ~60k tokens
**Complexity:** Medium

#### Scope
This plan file's appendix only. No manifest edits, no cluster writes, no load tests.

#### Detailed Steps
1. **Per-workload sizing.** tanguille-site `grafana_query_prometheus`, uid `prometheus`, instant. Group by workload, not ReplicaSet/Job: join `kube_pod_labels` on `label_app_kubernetes_io_name` (fallback `label_app`), and keep only pods that ran on control-2/3 with `* on(namespace,pod) group_left(node) max by(namespace,pod,node)(kube_pod_info{node=~"control-[23]"})`. For each workload/container record:
   - working set p99 and max over 30d (`quantile_over_time(0.99, container_memory_working_set_bytes{container!="",container!="POD"}[30d])`, `max_over_time(...)`)
   - RSS p99 (`container_memory_rss`), and shmem where applicable (postgres)
   - current request (`kube_pod_container_resource_requests{resource="memory"}`)
   - Sizing basis: working set p99, or RSS p99 (+shmem) when working set exceeds RSS by > 512Mi
   - Proposed = basis rounded up to the next power of two; Δ; file:line of the resources block, or SKIP if none
   Run per namespace if VictoriaMetrics times out on 30d.
2. **Per-process GTT.** On each iGPU node, for each llama-server process: `talosctl -n <ip> read /proc/<pid>/fdinfo/<fd>` and sum `drm-memory-gtt` across that process's DRM fds (find pids with `talosctl -n <ip> processes`). Record per model, now and after 6h. This also prototypes Chunk 3.
3. **Pick the reranker co-tenant.** From step 2, choose which anti-affinity term the reranker keeps: it should avoid the model with the larger GTT, and share a node with the smaller one.
4. **Exporter survey for Chunk 3.** Check whether the deployed drm-exporter (chart 0.3.4) or node-exporter can export per-client fdinfo with pod attribution (context7 / chart docs). Record the choice.
5. **apiserver and /podruntime.** apiserver p99 per node (done: 6.52 / 5.87 / 5.04 GiB on control-1/2/3). /podruntime p99 per node via `talosctl -n <ip> read /sys/fs/cgroup/podruntime/memory.current` sampled, or its cAdvisor series if scraped.
6. **Fit check per node** using `kube_node_status_allocatable{resource="memory"} - sum by(node)(kube_pod_container_resource_requests{resource="memory"} * on(namespace,pod) group_left() (kube_pod_status_phase{phase=~"Running|Pending"}==1))`: free room must cover Σ(pinned Δ) on that node (iGPU models, dragonfly +1.875 GiB, postgres +2 GiB, OSD/mon if changed). If it fails, list which movable pods must move first.

#### Verification
- [ ] Appendix filled, each row with query, value and file:line
- [ ] Per-process GTT recorded for all three iGPU models
- [ ] Fit check result per node recorded

#### Continuation Prompt
> Continue the RAM plan from `docs/plans/ram-optimization.md` in worktree `.claude/worktrees/ram-optimization` (branch `feat/ram-honest-requests`). First: `git -C .claude/worktrees/ram-optimization status && git -C .claude/worktrees/ram-optimization log --oneline -3`. Mark Chunk 1 complete. Check CRIT-1 is resolved (`kubectl -n ai get ks llmkube-models -o jsonpath='{.spec.suspend}'` is `false`), then run Chunks 2 and 3.

---

### Chunk 2: iGPU models
**Status:** Not Started
**Wave:** 2
**Estimated Context:** ~40k tokens
**Complexity:** Medium

#### Prerequisites
Chunk 1; CRIT-1 resolved and the 2048 reranker config live for ≥ 24h (re-measure GTT on it before editing).

#### Detailed Steps
1. **Pre-flight.** LLMKube Deployments use `strategy: Recreate`: the old pod dies before the new one is scheduled. For each changed model, the target node must have `allocatable − Σrequests ≥ new − old request`. The embedder can only run on the node without qwen35-2b.
2. **Reranker affinity.** Remove the anti-affinity term chosen in Chunk 1 step 3 (bge-reranker-v2-m3.yaml:76-88). Keep the remaining term preferred.
3. **Reranker size.** Keep `contextSize/uBatchSize 2048` (a pair must fit one micro-batch or llama.cpp returns 500s). Set `resources.memory` (LLMKube sets request and limit) to roundup_pow2(RSS p99 + GTT 30d max on the 2048 config); expect 2Gi. Only if GTT still grows > 1 GiB per day on 2048: shorten `maxPodLifetimeSeconds`, after measuring cold-start time and memini's behaviour on rerank failure, recorded in the commit.
4. **Embedder size: SKIPPED (owner, 2026-10-09: leave the embedding model for now).** `resources.memory` 512Mi → roundup_pow2(RSS p99 + GTT increment), expect 2Gi; rewrite the comment at :99-103.
5. Validate with flate; compare live InferenceService spec to git after reconcile.

#### Verification
- [ ] Reranker and embedder on different nodes; US-1 rerank latency check passes
- [ ] GTT < 4 GiB per iGPU node for 24h (`NodeGTTMemoryHigh` silent)

---

### Chunk 3: Per-pod GTT metric
**Status:** Not Started
**Wave:** 2
**Estimated Context:** ~50k tokens
**Complexity:** Medium

#### Detailed Steps
1. Implement the Chunk 1 step 4 choice. If no existing exporter fits: a DaemonSet on `amd.com/igpu: "true"` nodes, hostPID, read-only `/proc`, that maps pid → cgroup → pod uid and exports `pod_drm_memory_gtt_bytes{namespace,pod}` from `drm-memory-gtt` in fdinfo. Follow the repo's app-template + ServiceMonitor patterns and the existing exporter layout.
2. Update `NodeGTTMemoryHigh` description to include `topk(1, pod_drm_memory_gtt_bytes)` for the node.
3. Validate with flate; after owner-approved reconcile compare Σ per node to `node_drm_memory_gtt_used_bytes`.

#### Verification
- [ ] US-2 criteria met

---

### Chunk 4: Movable workloads
**Status:** Not Started
**Wave:** 3
**Estimated Context:** ~70k tokens
**Complexity:** Medium

#### Detailed Steps
1. For each movable appendix row, set `requests.memory` to Proposed. If a limit exists below the new request, stop and flag the row for the owner.
2. Skip single-replica PVC-backed workloads unless Δ > 1 GiB (never roll stateful for small gains).
3. flate test + diff (roll list equals rows); one commit per namespace: `fix(<ns>): size memory requests to 30d p99`.

#### Verification
- [ ] US-3 room and Pending criteria after the owner-approved reconcile

---

### Chunk 5: Stateful requests
**Status:** Not Started
**Wave:** 4
**Estimated Context:** ~40k tokens
**Complexity:** High

#### Detailed Steps
1. Pre-flight on EVERY node, before committing: free requested room ≥ 1.875 GiB (dragonfly) + 2 GiB (postgres16 instance on that node). Any node short: stop.
2. Pre-flight litellm advisory locks: `select pid,state from pg_locks l join pg_stat_activity using(pid) where locktype='advisory'` → 0 rows.
3. Edit both files, flate test + diff (only Dragonfly and Cluster), commit.
4. After the owner-approved reconcile: cnpg switchover completes, 3/3 instances Ready; dragonfly 1/1/1; no Pending > 10 min.

#### Verification
- [ ] postgres16 healthy (3 instances), dragonfly 3/3, gatus green for litellm, grafana, nextcloud, ghostfolio

---

### Chunk 6: Verify, decide, document
**Status:** Not Started
**Wave:** 5
**Estimated Context:** ~30k tokens
**Complexity:** Low

#### Detailed Steps
1. After 7 days: re-run the baseline queries; check US-1/2/3.
2. Decision 4 (apiserver) with fresh numbers; Decision 5 (descheduler `LowNodeUtilization` on memory requests) only if requests drift > 20% between nodes.
3. Re-evaluate the `KubeMemoryOvercommit` silence (silence-operator/silences/silences.yaml:16-25).
4. Consolidate into `docs/llm-hosting/embedder-cpu-vs-igpu.md` (GTT section) and the comment block at talos/cluster.yaml.j2:25-41 (kubelet blind to GTT, upstream memcg status), then delete this plan.

---

## Testing Strategy
- Static: flate test (308/308 baseline), validate-pr.sh, flate diff roll list, live-vs-git spec diff.
- E2E: US-1/2/3 acceptance criteria.

## Rollback Plan
1. One commit/PR per chunk: `git revert`, owner-approved reconcile.
2. Stateful: revert lowers requests only (second roll, no data impact).
3. Node starving mid-plan: delete the iGPU pod with the highest GTT on that node (runbook).

## Risks
- kubeReserved gap (/podruntime peaks to 14.1 GB) can still breach US-1.
- Live drift beyond llmkube (Finding 3) can make git-only reasoning wrong; always check live spec.

---

## Appendix: Sizing table

_Filled by Chunk 1._

| Namespace | Workload | Container | Request | WS p99 | WS max | RSS p99 | Basis | Proposed | Δ | File:line | Pinned? |
|---|---|---|---|---|---|---|---|---|---|---|---|

---

## Review report (Opus, 2026-10-09)

- CRIT-1 llmkube-models suspended, reranker fix never live: **OPEN, owner decision** (Prerequisites).
- CRIT-2 Chunk 1 load test on a starved node: **[FIXED]** removed; Chunk 1 is read-only, reranker GTT re-measured on the 2048 config after CRIT-1.
- HIGH-1 apiserver fit fails: **[FIXED]** moved to Decision 4 with a default.
- HIGH-2 Wave 2 room competition: **[FIXED]** Chunk 4 now waits for Chunk 2; Chunk 2 has a Recreate-aware pre-flight.
- HIGH-3 stateful pre-flight timing: **[FIXED]** every node, before commit; local-PV pinning noted.
- HIGH-4 working set includes cache: **[FIXED]** RSS-based basis.
- HIGH-5 kubeReserved gap: **[FIXED]** Finding 8 + Risks + /podruntime measurement.
- MED-1/2 reranker ubatch/lifetime: **[FIXED]** keep 2048; lifetime fallback gated on measurements.
- MED-3 per-model GTT attribution: **[FIXED]** per-process fdinfo.
- MED-4 sizing query grouping: **[FIXED]** app label + node filter.
- MED-5 Talos cpu default: moot while Decision 4 defaults to (a); if (b)/(c), set `requests: {cpu: 200m, memory: X}` explicitly.
- MED-6 affinity before data: **[FIXED]** Chunk 1 step 3.
- MED-7 embedder reverses owner decision: **[FIXED]** owner OK required.
- LOW-1..6: line numbers, 512Mi default, kopiur mover SKIP, 2Gi reranker, rationale, Talos drift check: **[FIXED]** (Talos chunk removed; `just talos diff-node` drift check applies if Decision 4 becomes (b)/(c)).

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
