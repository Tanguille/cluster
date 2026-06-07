# Fix All Open Alerts — Implementation Plan

**Created:** 2026-06-07
**Status:** Implemented (all 7 chunks committed on `fix/cluster-alerts`, 2026-06-07) — awaiting merge, reconcile, and post-deploy verification
**Complexity:** Medium
**Estimated Chunks:** 7 across 4 waves
**Branch:** `fix/cluster-alerts` (branch from `main`, NOT from `feat/gpu-operator-oci`)
**Worktree:** create with `git worktree add .claude/worktrees/fix-cluster-alerts -b fix/cluster-alerts origin/main`, then copy `.mcp.json`, `.env` (if present), `CLAUDE.local.md` (if present), `.vscode/`, and `.claude/` into the worktree.

---

## Overview

35 alerts are firing (plus one broken-but-unalerted component). Root causes were investigated live on 2026-06-07 with every claim adversarially re-verified. Three incidents trace back to the **Rook v1.19.6 → v1.20.0 upgrade on 2026-06-06** (commit `240a9f7eb`): backups silently stopped (18 critical alerts), a Ceph alert rule started erroring (4 warning alerts), and a manual troubleshooting `kubectl apply` left a stray HelmRelease (1 critical alert). The remainder are capacity/by-design signals and one wedged MCP aggregator.

## Root-Cause Summary

| # | Alert family | Severity | Root cause (verified) | Type |
|---|---|---|---|---|
| 1 | VolSyncVolumeOutOfSync ×18 | critical | New `ceph-csi-drivers` chart (Rook v1.20.0 split) defaults `drivers.rbd.snapshotPolicy: none` → RBD csi-snapshotter sidecar removed → all VolumeSnapshots stuck `ReadyToUse=false` → **backups stopped since 2026-06-06T00:01Z** | Regression |
| 2 | HelmReleaseReconciliationFailure (`qui`) | critical | Stray hand-`kubectl apply`'d HelmRelease in `default` ns (applied without `-n media` on 06-06 20:22). Not Flux-owned → prune never removes it. Real `media/qui` is healthy. | Drift |
| 3 | NodeSystemSaturation / NodeDiskIOSaturation / NodeCPUHighUsage (control-1) | warning/info | llama-server's 16 GB MoE model is mmap'd from a PVC on control-1's slow virtio system disk `vda`; page-cache eviction re-faults it at 400–1000 MB/s for 40–50 min per inference session. Amplified by xmrig spawning 12 threads against a 6-core quota. | Perf defect |
| 4 | KubeCPUOvercommit / KubeMemoryOvercommit | warning | N-1 rule. Live baseline: 26.21 cores requested vs 22-core N-1 capacity. CPU excess = inflated requests (crowdsec, CNPG, xmrig, CSI sidecars — trimmable). Memory excess (17.9 GiB) = **structural**: control-1 (39 GiB, only GPU node) dwarfs peers (21 GiB); its loss is unsurvivable regardless. | Mixed |
| 5 | KubeHpaMaxedOut ×2, CPUThrottlingHigh ×3 (xmrig) | warning/info | Solar-surplus miner at KEDA max = by design; **already silenced** (never notifies). ×2 = orphaned `kube-prometheus-stack-hpa-rules` PrometheusRule from decommissioned release (drift). Throttling = 12 threads vs 6-core quota (real defect, fixable). | By design + drift |
| 6 | AlertingRulesError, TooManyLogs ×2, RequestErrorsToAPI ×2 | warning | One broken rule: `CephPoolGrowthWarning` 422s because the mgr pod roll (host networking → same `instance`, different `pod`) creates duplicate series in its `[2d]` lookback. Self-heals ~2026-06-08 11:41 UTC, but **recurs after every mgr roll**. ×2 on RequestErrorsToAPI = chart deploys unused `vmcluster` rule group alongside `vmsingle`. | Fragile rule |
| 7 | vmcp-unified hangs (no alert!) | — | Optimizer re-embeds 187 tool descriptions through CPU-only TEI on **every** MCP initialize (32–38 s) > vmcp's ~30 s response-write deadline → client sees infinite hang. Backends are all healthy. Restarts don't help. | Config defect |

Full evidence: `/tmp/alert-findings.json` (session artifact; evidence embedded per finding).

---

## Decisions (per-alert)

> ☑ = recommendation, following the repo's AGENTS.md preference: *"Prefer fixing root causes over silencing alerts."*
> **Before execution starts, record here:** `Decisions D1–D7 confirmed as marked by user on <date>` — then executors must NOT re-ask per chunk.

### D1 — VolSync (no real choice; fix is mandatory)
- ☑ **A: Re-enable RBD snapshots** via `drivers.rbd.snapshotPolicy: volumeSnapshot` + pin cephfs explicitly. Backups resume automatically; all 18 alerts clear.
- ☐ B (additive, optional): demote per-volume rule to `warning` + add aggregate `count(volsync_volume_out_of_sync == 1) > 3` at `critical` so a shared-cause outage pages once, not 18×. *Default: skip — alert behaved correctly.*

### D2 — Stray `qui` HelmRelease
- ☑ **A: one-time `kubectl delete helmrelease qui -n default`** (with the pre-checks in Chunk 3). No repo change possible — object is not Flux-owned.
- ☐ B: silence — strictly worse, helm-controller retries forever.

### D3 — control-1 saturation
- ☑ **A: `no-mmap = true`** for qwen-3.6 in `models.ini` + raise llama-server memory request 6Gi→**16Gi** and CPU request 1→**2** (CFS weight vs miner; values capped by control-1 schedulability — see Chunk 4 pre-flight).
- ☐ B: `mlock = true` instead of no-mmap — keeps fast reloads but needs IPC_LOCK capability + memlock rlimit verification under Talos.
- ☐ C: silence the node alerts — **rejected**: vda also hosts etcd + rook mon; masking queue-depth>10 there hides control-plane risk.
- ⚠️ Memory note `project_llama_server_gemma_moe_design.md` says qwen-3.6 "needs mmap" — that applied to the deprecated ik_llama setup. Re-verify model load after the change; update the memory file once verified.

### D4 — Overcommit
- ☑ **A (CPU): trim the four inflated requests** (crowdsec, CNPG, xmrig, CSI sidecars). Honest arithmetic (live baseline 26.21 cores, N-1 = 22): −2.85 xmrig, −1.6 crowdsec, −1.5 CNPG, −1.5..2.0 CSI, **+1.0 llama-server (Chunk 4), +0.2 returning csi-snapshotter sidecars (Chunk 1)** → post-plan ≈ **20.0–20.5 cores**, ~1.5–2 core margin. The `<22` check is only meaningful after Chunks 1, 4, 5, 6 have all reconciled.
- ☑ **B (Memory): silence `KubeMemoryOvercommit`** via silence-operator — structural on this hardware (control-1 asymmetry), unfixable by tuning; rule stays visible in vmalert.
- ☐ C: also disable rule chart-side (`defaultRules.rules`) — less discoverable than a Silence.

### D5 — xmrig
- ☑ **A: thread cap `--cpu-max-threads-hint=50`** → CPUThrottlingHigh resolves naturally (no silence needed), load average drops on all 3 nodes.
- ☑ **B: delete orphaned `kube-prometheus-stack-hpa-rules`** PrometheusRule (one-time op; kills the duplicate KubeHpaMaxedOut).
- ☐ C: lower KEDA `maxReplicaCount` 3→2 to keep miners off control-1 — *default: skip; D3's CFS-weight fix + existing −1000 PriorityClass should suffice. Revisit if saturation persists.*
- ☐ D: silence CPUThrottlingHigh for xmrig — only via the explicit gate in Chunk 7 step 4.

### D6 — VictoriaMetrics self-monitoring cascade
- ☑ **A: fix the `CephPoolGrowthWarning` expr** (`max without (pod) (...)`) — rewritten query verified to execute cleanly against live vmsingle. Without it the 2-day alert storm recurs after **every** mgr pod roll. *Amended in the simplify pass: implemented via the chart-native `monitoring.prometheusRuleOverrides` value (merge by alert name, render-verified), replacing the originally planned positional postRenderers patch — no index fragility, no re-verification on chart bumps.*
- ☑ **B: disable unused `vmcluster` default rule group** (kills duplicate RequestErrorsToAPI + ~13 dead rules).
- ☐ C: do nothing — self-heals 2026-06-08 ~11:41 UTC but recurs. *Rejected.*

### D7 — vmcp-unified — **user decision 2026-06-07: optimizer is in active use; keep it**
- ☐ A: remove the optimizer — **declined by user** (the semantic tool discovery is being used).
- ☑ **B: tune TEI so the 187-tool embed fits the ~30 s response window** (batch-size 256, max-batch-tokens 65536, cpu limit 2→4 → per-session indexing drops from 32–38 s to ~1–5 s). Caveat: per-session re-embedding remains; the latency cliff returns if the toolset grows or TEI is contended → pair with the upstream-tracking follow-up.
- ☒ C: retire the optimizer experiment — **rejected by user, it is in use.** Do not propose removing the `-opt` backends/TEI.
- ☑ **D: add gatus POST /mcp initialize probes** — proven gap: /health green while /mcp dead. With the optimizer kept, each unified probe triggers one re-embed (~1–5 s of TEI CPU per 5 min after the tuning — acceptable; the other 6 vmcps have no optimizer).

---

## Execution Plan

### Progress Tracker

#### Wave 1 (parallel) — Critical: backups, drift, broken MCP
- [x] Chunk 1: Restore RBD snapshot support (clears 18× VolSyncVolumeOutOfSync) — commit `99c19a915`
- [x] Chunk 2: Fix vmcp-unified + gatus /mcp probes — commit `b98ab68f2`
- [x] Chunk 3: Drift cleanup + repo housekeeping — commit `0a10bb062`; cluster ops executed 2026-06-07 (stray `default/qui` HR, orphaned PrometheusRule + admission secret deleted after all pre-checks passed; `media/qui` verified untouched)

#### Wave 2 (parallel) — CPU trims *(Chunk 6 requires Chunk 1 verified first)*
- [x] Chunk 5: xmrig thread cap + request trim — commit `45e8cb8bd` *(user override: cpu request 10m, not 50m)*
- [x] Chunk 6: Trim inflated CPU requests — commit `59aec6436` (CSI trim render-verified against chart 1.0.1: both Driver CRs keep snapshotPolicy, 14 container keys, zero nulls)

#### Wave 3 (sequential) — Needs the headroom from Wave 2
- [x] Chunk 4: llama-server mmap thrash fix — commit `2160a0ac4` *(user override: cpu request stays 1; memory 16Gi as planned)*. Pre-flight passed live: post-merge control-1 ≈ 8.0/11 cores, ~34.6/39 Gi (~4.4 Gi headroom)

#### Wave 4 (sequential) — Alert hygiene
- [x] Chunk 7: CephPoolGrowthWarning patch, vmcluster group disable, KubeMemoryOvercommit silence, housekeeping — commit `a839d2d52`; simplify pass replaced the positional postRenderers patch with chart-native `monitoring.prometheusRuleOverrides` (render-verified by alert name). Step 4 (CPUThrottlingHigh silence) gated — decide ≥6 h after Chunk 5 deploys

> All chunks landed on one branch, so Wave-ordering concerns (Chunk 1 → 6 same-file, mid-drain CSI roll) collapse into a single reconcile — the snapshotter returns already-trimmed in one ctrlplugin roll.

### Wave Conflict Matrix

| Chunk | Write set | Dependencies | Wave |
|---|---|---|---|
| 1 | `kubernetes/apps/rook-ceph/rook-ceph/csi/helmrelease.yaml` | None | 1 |
| 2 | `kubernetes/apps/ai/toolhive/config/embeddingserver.yaml`, `kubernetes/apps/observability/gatus/app/resources/config.yaml` | None | 1 |
| 3 | `validate-pr.sh`, `.mise.toml`, `AGENTS.md`, `.agents/**` docs (+ kubectl ops) | None | 1 |
| 5 | `kubernetes/apps/web3/monero/xmrig/helmrelease.yaml` | None | 2 |
| 6 | `kubernetes/apps/rook-ceph/rook-ceph/csi/helmrelease.yaml`, `kubernetes/apps/security/crowdsec/app/helmrelease.yaml`, `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` | **Chunk 1 merged + verified** (same file; and the 18-snapshot backlog must drain before CSI pods roll again) | 2 |
| 4 | `kubernetes/apps/ai/llama-server/app/config/models.ini`, `kubernetes/apps/ai/llama-server/app/helmrelease.yaml` | **Chunks 5 + 6 deployed** (control-1 headroom) + pre-flight check | 3 |
| 7 | `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`, `kubernetes/apps/observability/victoria-metrics/app/helmrelease.yaml`, `kubernetes/apps/observability/silence-operator/silences/silences.yaml`, `kubernetes/apps/gpu-operator/gpu-operator/app/prometheusrule.yaml` | Chunk 5 (step-4 gate) | 4 |

Within each wave: zero write-set overlap. Chunk 6 shares `csi/helmrelease.yaml` with Chunk 1 across waves — strict ordering enforced by its dependency.

---

### Chunk 1: Restore RBD snapshot support
**Wave:** 1 · **Complexity:** Low · **Fixes:** VolSyncVolumeOutOfSync ×18 (critical) — backups stopped since 2026-06-06

#### Steps
1. In `kubernetes/apps/rook-ceph/rook-ceph/csi/helmrelease.yaml`, **insert exactly two lines** (everything else — `kernelMountOptions`, `nfs: {enabled: false}`, `nvmeof: {enabled: false}` — stays untouched):
   ```diff
    drivers:
      rbd:
        name: rook-ceph.rbd.csi.ceph.com
   +    # chart default is "none" — dropping this removes the csi-snapshotter sidecar and breaks all VolSync RBD backups
   +    snapshotPolicy: volumeSnapshot
        controllerPlugin:
          replicas: 2
      cephfs:
        name: rook-ceph.cephfs.csi.ceph.com
   +    # explicit pin; equals chart 1.0.1 default — defaults proved inconsistent between drivers
   +    snapshotPolicy: volumeSnapshot
        controllerPlugin:
          replicas: 2
   ```
   (Must be per-driver — `operatorConfig.driverSpecDefaults` is ignored by the chart's `templates/driver.yaml`.)
2. Commit (`fix(rook-ceph): re-enable rbd snapshotPolicy dropped by ceph-csi-drivers chart split`), push, then `flux reconcile kustomization rook-ceph-csi -n flux-system --with-source` (Kustomization name verified: `rook-ceph-csi`, second document in `kubernetes/apps/rook-ceph/rook-ceph/ks.yaml`).

#### Verification (E2E)
- [ ] `kubectl get pod -n rook-ceph -l app=rook-ceph.rbd.csi.ceph.com-ctrlplugin -o jsonpath='{.items[*].spec.containers[*].name}'` includes `csi-snapshotter` (note: these pods have no `app.kubernetes.io/name` label — use `app=`)
- [ ] The 18 stuck `volsync-<app>-src` VolumeSnapshots become `READYTOUSE=true`: `kubectl get volumesnapshots -A | grep volsync`
- [ ] Blocked syncs complete: `kubectl get replicationsources -A` shows fresh `LAST SYNC` timestamps (volsync is still reconciling; no manual trigger needed)
- [ ] **IO herd watch:** all 18 snapshots cut + 18 kopia movers start near-simultaneously. Watch `ceph health` (toolbox) and control-1 load while the backlog drains. **Do not start Wave 2 until LAST SYNC is fresh on all 18** — this is a hard ordering requirement, not a nicety.
- [ ] All 18 alerts clear: `count(volsync_volume_out_of_sync == 1)` → 0 (or empty)
- [ ] **Backup integrity:** `kubectl get replicationsource -n media radarr -o jsonpath='{.status.latestMoverStatus}'` → `SUCCESS` with a recent time

#### Rollback
Revert the commit; the Driver CR returns to `snapshotPolicy: none` (broken-backup state, no worse than before).

---

### Chunk 2: Fix vmcp-unified + add real MCP probes
**Wave:** 1 · **Complexity:** Low-Medium · **Fixes:** wedged unified MCP (no alert today — that's part of the problem)

> **Mechanism recap:** every MCP initialize re-embeds all 187 aggregated tool descriptions through the CPU-only TEI EmbeddingServer. Today that takes 32–38 s (the 187-text upsert splits into 6 sequential 32-item round trips at ~5–7 s each on a 2-CPU limit), blowing past vmcp's ~30 s response-write deadline → the response is never written. The optimizer stays (user decision D7-B); the fix is making the embed fast.

#### Steps
1. *(Per D7-B)* In `kubernetes/apps/ai/toolhive/config/embeddingserver.yaml` (`toolhive-embeddings`):
   - add TEI args so the upsert is one batch instead of 6 chunked round trips (`spec.args` is a native EmbeddingServer CRD field — verified via `kubectl explain`):
     ```yaml
     spec:
       args:
         # default --max-client-batch-size is 32: vmcp's 187-tool upsert splits into 6 sequential
         # round trips (~5-7s each = 32-38s total), exceeding vmcp's ~30s response-write deadline
         - "--max-client-batch-size"
         - "256"
         - "--max-batch-tokens"
         - "65536"
     ```
   - raise `resources.limits.cpu` `"2"` → `"4"` (keep requests as-is — limits don't count toward the D4 overcommit math).
   Expected result: per-session indexing ~1–5 s, well under the deadline. The operator rolls the TEI pod on CR change.
2. In `kubernetes/apps/observability/gatus/app/resources/config.yaml`, add **all 7** vmcp endpoints under a new `ai` group: `vmcp-database`, `vmcp-flux`, `vmcp-ha`, `vmcp-observability`, `vmcp-resources`, `vmcp-search`, `vmcp-unified` — all on port 4483, identical template, only the name/url varying. The file currently has only ICMP connectivity checks, so this template IS the convention for HTTP probes (double-quote conditions to match the existing entries' string style):
   ```yaml
   - name: vmcp-unified-mcp
     group: ai
     url: http://vmcp-unified.ai.svc.cluster.local:4483/mcp
     method: POST
     headers:
       Content-Type: application/json
       Accept: application/json, text/event-stream
     body: '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"gatus","version":"0"}}}'
     interval: 5m
     client:
       timeout: 15s
     conditions:
       - "[STATUS] == 200"
       - "[RESPONSE_TIME] < 10000"
     alerts:
       - type: discord
         enabled: true
         send-on-resolved: true
   ```
   The body contains no `${`; if you add any literal `${VAR}`, escape as `$${VAR}` (Flux postBuild envsubst — the file already uses this pattern).
3. Commit (`fix(toolhive): tune tei batching so vmcp-unified initialize fits the response window`), push, reconcile.

#### Verification (E2E)
- [ ] TEI pod rolled with the new args: `kubectl get pod -n ai toolhive-embeddings-0 -o jsonpath='{.spec.containers[0].args}'` includes `--max-client-batch-size 256`
- [ ] Port-forward `svc/vmcp-unified 4483` and POST an MCP initialize → JSON-RPC result in **<10 s, target 1–5 s** (was: HTTP 000 after 33–38 s). TEI logs should show the upsert as 1 batch, not 6 sequential ~5–7 s chunks
- [ ] From the workstation: `claude mcp list` → `unified: ✓ Connected`; ToolSearch in a fresh session finds unified tools (the original user-visible failure)
- [ ] **Optimizer still functional:** tools/list on the new session returns the optimizer meta-tools (find_tool/call_tool), not all 187 raw tools
- [ ] Gatus UI shows all 7 `ai` group endpoints green
- [ ] **Session accumulation:** ~1 h after probes go live, confirm `thv:vmcp:*:session:*` keys in dragonfly are bounded (TTL'd), e.g. via dragonfly metrics/key-count trend — 7 probes × 12/h create ~2k sessions/day if never expired; if unbounded, lengthen `interval` to 15m
- [ ] **If initialize still exceeds ~15 s** under load (e.g. several clients reconnecting simultaneously): stop and escalate — options are TEI `replicas: 2` or revisiting D7 with the user; do not silently remove the optimizer

#### Rollback
Revert the commit — TEI returns to default batching (and unified to the hang). Nothing else depends on the new args.

#### Follow-ups (out of chunk scope)
- **Track upstream toolhive** (`kubernetes/apps/ai/toolhive/app/ocirepository.yaml`, currently 0.29.1) for cross-session optimizer caching or startup-time indexing — the per-session re-embed remains the structural weakness; bump the tag when it lands.
- Upstream: consider filing a stacklok/toolhive issue with the measured timings (session-scoped optimizer re-embeds 187 tools per initialize at 32–38 s; ~30 s write deadline swallows the response with HTTP 000).

---

### Chunk 3: Drift cleanup + repo housekeeping
**Wave:** 1 · **Complexity:** Low · **Fixes:** HelmReleaseReconciliationFailure (critical), KubeHpaMaxedOut duplicate, broken validation gate

> The cluster objects were created out-of-band; Flux prune will never remove them. Run by hand, with the pre-checks — they are the safety net.

#### Steps
1. **Stray qui HelmRelease** — verify it is still the stray AND that no helm release storage exists, then delete with a bounded wait:
   ```bash
   # MUST show: no kustomize.toolkit.fluxcd.io labels, observedGeneration: -1,
   # and a kubectl.kubernetes.io/last-applied-configuration annotation
   kubectl get helmrelease qui -n default -o yaml | grep -E 'kustomize.toolkit|observedGeneration|last-applied'
   # MUST be empty — proves helm-controller finalization cannot uninstall anything
   kubectl get secret -n default -l 'owner=helm,name=qui'
   kubectl delete helmrelease qui -n default --timeout=60s
   # if it times out, inspect helm-controller logs; do NOT force-remove the finalizer
   ```
   Do NOT touch `media/qui` (the healthy, Flux-owned one).
2. **Orphaned PrometheusRule + admission secret** from the decommissioned kube-prometheus-stack (both verified orphans of the same dead release, no consumers):
   ```bash
   kubectl delete prometheusrule -n observability kube-prometheus-stack-hpa-rules
   kubectl delete secret -n observability kube-prometheus-stack-admission
   ```
3. **Restore the validation gate** *(amended 2026-06-07, user decision: remove yamllint rather than configure it — `kustomize build` already catches YAML syntax errors and duplicate keys, verified empirically; style is owned by yamlfmt/flate)*:
   - `validate-pr.sh`: drop the yamllint phase (renumber to [1/4]–[4/4]); fix the pre-existing `shellcheck "$SHELL_SCRIPTS"` quoting bug (whole list passed as one filename → always failed with >1 script); exclude `${REPO_ROOT}/.claude/*` (anchored, so running from a worktree still scans the tree) and `${REPO_ROOT}/archive/*` (retired scripts) from the shellcheck sweep.
   - `.mise.toml`: remove the yamllint pin.
   - Update the ~9 doc references (`AGENTS.md`, `.agents/common-operations.md`, `.agents/skills/{pr-review,add-app-to-cluster,git-worktree-isolation}/**`) to point at `kustomize build` instead.
   Then confirm: `bash .agents/skills/pr-review/scripts/validate-pr.sh` exits 0 on an untouched tree.

#### Verification (E2E)
- [ ] `kubectl get hr -A | grep qui` → only `media/qui`, Ready=True
- [ ] HelmReleaseReconciliationFailure clears in vmalert within one kube-state-metrics scrape
- [ ] `kubectl get vmrule -n observability | grep kube-prometheus-stack` → empty (operator GCs the converted VMRule)
- [ ] `kubectl get secret -n observability kube-prometheus-stack-admission` → NotFound
- [ ] KubeHpaMaxedOut fires ×1 (still silenced — expected; it is by-design)
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` exits 0

#### Rollback
Cluster objects are recreatable from `git show` / chart templates if somehow needed; neither owns any workload. The validation-gate changes are a plain revert.

---

### Chunk 5: xmrig thread cap + scavenger request
**Wave:** 2 · **Complexity:** Low · **Fixes:** CPUThrottlingHigh ×3 (real fix, no silence needed); contributes headroom to chunks 4 + 6

#### Steps
1. In `kubernetes/apps/web3/monero/xmrig/helmrelease.yaml`:
   - Add to the container args list, matching the file's quoted-and-commented arg style:
     ```yaml
     # Cap mining threads to the CFS quota: 50% of 12 host cores = 6 threads = cpu limit.
     # Without this xmrig spawns 12 threads and is throttled 90-100% of periods.
     # Revisit if the cpu limit or node core count changes.
     - "--cpu-max-threads-hint=50"
     ```
   - cpu request `1` → `50m` (PriorityClass `low-priority-mining` value −1000 already makes it the sacrificial workload; the 1-core request only inflates overcommit math and gives it undeserved CFS weight). Saves ~2.85 cores at 3 replicas.
2. *(D5-C declined by default — `maxReplicaCount` stays 3; revisit if control-1 saturation persists after Chunk 4.)*
3. Commit (`fix(xmrig): cap mining threads to cfs quota and trim scavenger cpu request`), push, reconcile.

#### Verification (E2E)
- [ ] `rate(container_cpu_cfs_throttled_periods_total{namespace="web3"}[5m]) / rate(container_cpu_cfs_periods_total{namespace="web3"}[5m])` drops from 0.9–1.0 to < 0.25 → CPUThrottlingHigh ×3 resolves naturally
- [ ] Hashrate unchanged or better (xmrig dashboard); hugepages still fit in the 3Gi limit
- [ ] `node_load1` baseline drops ~6 on each node

#### Rollback
Revert commit. (Throttling alerts return — they're info-level.)

---

### Chunk 6: Trim inflated CPU requests
**Wave:** 2 (after Chunk 1 verified) · **Complexity:** Medium · **Fixes:** KubeCPUOvercommit (see D4-A for the honest arithmetic — the `<22` gate needs Chunks 1+4+5+6 all landed)

#### Steps
1. `kubernetes/apps/security/crowdsec/app/helmrelease.yaml` — add **CPU-only** requests under the existing `lapi:` (~line 117) and `agent:` (~line 141) sections:
   ```yaml
   resources:
     requests:
       cpu: 100m
   ```
   Do NOT set memory requests — the working set was never measured; Helm deep-merge keeps chart-default memory. (Observed: 2–13 millicores per pod vs 500m requested × 4 pods → saves 1.6 cores.)
2. `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` — `spec.resources.requests.cpu: 1` (~line 90) → `500m` per instance. Keep `limits.cpu: 4`. Saves 1.5 cores.
   ⚠️ **Expect a controlled switchover**: the cluster sets `primaryUpdateStrategy: unsupervised` + `primaryUpdateMethod: switchover`, so changing the pod template rolls all 3 instances and deliberately moves the primary — a brief write interruption for every postgres-backed app. Prefer a low-traffic window.
3. **CSI sidecar trim — target the `ceph-csi-drivers` chart, NOT the operator chart.** The rook-ceph operator chart removed `csiRBDProvisionerResource`/`csi*PluginResource` values in v1.20.0 (verified: zero hits in chart values/templates — setting them there is a silent no-op). CSI pods are rendered from Driver CRs owned by the `ceph-csi-drivers` release. In `kubernetes/apps/rook-ceph/rook-ceph/csi/helmrelease.yaml` (same file as Chunk 1 — hence the dependency), add under **both** `drivers.rbd` and `drivers.cephfs`:
   ```yaml
   controllerPlugin:
     replicas: 2
     resources:
       # chart renders ALL container keys once resources is non-empty — every key must be present
       # requests-only (no limits): ~2x observed steady-state; defaults were 100m/128Mi per sidecar
       attacher: {requests: {cpu: 50m, memory: 64Mi}}
       snapshotter: {requests: {cpu: 50m, memory: 64Mi}}
       resizer: {requests: {cpu: 50m, memory: 64Mi}}
       provisioner: {requests: {cpu: 50m, memory: 64Mi}}
       omapGenerator: {requests: {cpu: 50m, memory: 64Mi}}
       liveness: {requests: {cpu: 50m, memory: 64Mi}}
       addons: {requests: {cpu: 50m, memory: 64Mi}}
       logRotator: {requests: {cpu: 50m, memory: 64Mi}}
       plugin: {requests: {cpu: 100m, memory: 256Mi}}
   nodePlugin:
     resources:
       registrar: {requests: {cpu: 25m, memory: 32Mi}}
       liveness: {requests: {cpu: 50m, memory: 64Mi}}
       addons: {requests: {cpu: 50m, memory: 64Mi}}
       logRotator: {requests: {cpu: 50m, memory: 64Mi}}
       plugin: {requests: {cpu: 100m, memory: 128Mi}}
   ```
   (`templates/driver.yaml` renders every sub-key unconditionally once `resources` is set — omitted keys render `null`, so the complete map is mandatory. Saves ~1.5–2 cores and several GiB vs the ~3.7 cores the CSI pods request today.)
4. Commit (`fix(resources): trim inflated cpu requests (crowdsec, cnpg, ceph-csi sidecars)`), push, reconcile.

#### Verification (E2E)
- [ ] Crowdsec lapi/agents roll and pass readiness
- [ ] CNPG: **one controlled switchover is expected and normal.** Verify: `kubectl get cluster -n database postgres16` returns "Cluster in healthy state" with 3/3 ready; `.status.targetPrimary == .status.currentPrimary`; replica logs show streaming reattach (NO `pg_basebackup` re-clone — storage is node-local openebs-hostpath); no *unplanned* failover events
- [ ] New CSI requests landed: `kubectl get pod -n rook-ceph -l app=rook-ceph.rbd.csi.ceph.com-ctrlplugin -o jsonpath='{.items[0].spec.containers[*].resources.requests}'` shows the trimmed values
- [ ] PVC provisioning still works: create + bind a 1Gi test PVC in default ns, then delete it
- [ ] After Chunks 4+5 also land: `sum(namespace_cpu:kube_pod_container_resource_requests:sum)` ≈ 20.0–20.5 < 22 → KubeCPUOvercommit resolves (record the actual number here)

#### Rollback
Revert commit per-file; each trim is independent.

---

### Chunk 4: Stop llama-server mmap page-cache thrash
**Wave:** 3 (requires Chunks 5 + 6 deployed) · **Complexity:** Medium · **Fixes:** NodeSystemSaturation, NodeDiskIOSaturation, NodeCPUHighUsage (control-1)

> **Why the dependency:** control-1 allocatable is 11 CPU / ~39.0Gi, with ~9.0 CPU / ~25.2Gi already requested (2026-06-07 live). The original 4-CPU/20Gi targets exceeded BOTH dimensions, and the Deployment uses `strategy: Recreate` with GPU-pinned nodeAffinity — a non-fitting request takes llama-server down and leaves it Pending with no automatic recovery (no Flux remediation configured, nothing preemptible enough to rescue it).

#### Steps
0. **Pre-flight (mandatory):** `kubectl describe node control-1 | grep -A8 'Allocated resources'` — confirm `requested CPU + 2000m ≤ 11000m` AND `requested memory + 10Gi (the 16Gi−6Gi delta) ≤ 40913080Ki`. If it doesn't fit, stop and find another trim (do NOT touch the `-opt` pods/TEI — the optimizer experiment is in active use per D7); dropping the memory request to 14Gi is the fallback.
1. In `kubernetes/apps/ai/llama-server/app/config/models.ini`, under `[qwen-3.6]`, add (the file documents every non-obvious setting with `;` rationale comments — follow that convention):
   ```ini
   ; Load expert tensors into anonymous RAM instead of mmap'ing the GGUF from the PVC:
   ; control-1's openebs-hostpath PVC sits on the slow virtio system disk (vda), and page-cache
   ; eviction caused 400-1000 MB/s re-fault storms for the whole inference session
   ; (NodeDiskIOSaturation/NodeSystemSaturation). The old "qwen needs mmap" note was ik_llama-era.
   no-mmap = true
   ```
2. In `kubernetes/apps/ai/llama-server/app/helmrelease.yaml`:
   - memory request `6Gi` → `16Gi` (scheduler honesty: ~13–14 GB expert tensors + 4 GB cache-ram live in anonymous RAM after no-mmap; 16Gi is the verified-schedulable value, NOT 20Gi)
   - cpu request `1` → `2` (CFS shares: inference wins contention 2:1 vs the trimmed 50m miner; 4 did not fit the node)
3. Commit (`fix(llama-server): load qwen-3.6 without mmap to stop vda page-cache thrash`), push, reconcile.

#### Verification (E2E)
- [ ] Pod is scheduled and Running (not Pending!) — `kubectl get pods -n ai -l app.kubernetes.io/name=llama-server -o wide`
- [ ] Model loads cleanly (`kubectl logs -n ai deploy/llama-server | tail`) — re-verify because of the old ik_llama-era "needs mmap" note
- [ ] Run one real inference (open-webui or curl the completion endpoint) — response sane
- [ ] `kubectl top node` — control-1 memory ≤ ~85%; if tight, reduce `cache-ram` from 4096 to 2048 in models.ini
- [ ] **Mark the chunk "deployed — alert verification pending" and continue; do NOT block.** The saturation trio can only prove itself during an inference session (sessions recur ~23:35 and ~11:53 local — origin untraced, likely a scheduled agent; identifying the caller is an open follow-up). After the next session: `max_over_time(ALERTS{alertname=~"NodeSystemSaturation|NodeDiskIOSaturation|NodeCPUHighUsage", alertstate="firing"}[6h])` → empty for control-1, and `rate(node_disk_io_time_seconds_total{device="vda"}[5m])` stayed low during the session
- [ ] **Then update the memory file** (absolute path — it lives outside the repo): `~/.claude/projects/-home-tanguille-Documents-PriveProjecten-cluster/memory/project_llama_server_gemma_moe_design.md` — replace the "needs mmap" guidance with "mainline router uses `no-mmap = true`; ik_llama-era mmap advice obsolete"

#### Rollback
Revert commit; pod rolls back to mmap behavior (saturation returns but nothing breaks).

---

### Chunk 7: Alert hygiene
**Wave:** 4 · **Complexity:** Medium · **Fixes:** AlertingRulesError, TooManyLogs ×2, RequestErrorsToAPI ×2, KubeMemoryOvercommit; prevents recurrence

#### Steps
1. *(Per D6-A, as amended in the simplify pass)* `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` — override the rule by **alert name** via the chart-native mechanism (rook-ceph-cluster `templates/prometheusrules.yaml` does a `mergeOverwrite` keyed on alert name):
   ```yaml
   values:
     monitoring:
       prometheusRuleOverrides:
         # Host-networked mgr pods share the instance label; after a mgr roll the [2d]
         # lookback returns duplicate series per pool and the join 422s for 2 days.
         # max without (pod) collapses the stale-pod duplicate before the join.
         CephPoolGrowthWarning:
           expr: "(max without (pod) (predict_linear(ceph_pool_percent_used[2d], 3600 * 24 * 5)) * on(cluster, pool_id, instance) group_right() ceph_pool_metadata) >= 95"
   ```
   Render-verified against the v1.20.0 chart: the override lands on `CephPoolGrowthWarning` in group `pools` with no positional dependency. *(The originally planned positional postRenderers JSON6902 patch — `/spec/groups/7/rules/0/expr` — was replaced by this; it carried index-drift risk on every chart bump and a blocked-HelmRelease blast radius affecting 4 dependent Kustomizations.)* Still check `flux get hr rook-ceph-cluster -n rook-ceph` → Ready=True right after merge.
2. *(Per D6-B)* `kubernetes/apps/observability/victoria-metrics/app/helmrelease.yaml` — extend the existing `defaultRules` block (~line 72):
   ```yaml
   defaultRules:
     rules:
       etcdMemberCommunicationSlow:
         create: false
     groups:
       vmcluster:
         create: false  # vmsingle deployment; the vmcluster group duplicates RequestErrorsToAPI etc.
   ```
3. *(Per D4-B)* `kubernetes/apps/observability/silence-operator/silences/silences.yaml` — append, matching the file's conventions exactly (schema header after `---`; equality matchers omit `matchType`):
   ```yaml
   ---
   # yaml-language-server: $schema=https://k8s-schemas.home-operations.com/observability.giantswarm.io/silence_v1alpha2.json
   apiVersion: observability.giantswarm.io/v1alpha2
   kind: Silence
   metadata:
     name: kube-memory-overcommit-homelab
   spec:
     matchers:
       # structural: control-1 (39Gi, only GPU node) dwarfs control-2/3 (21Gi);
       # N-1 memory capacity is unfixable by request tuning
       - name: alertname
         value: KubeMemoryOvercommit
   ```
4. **CPUThrottlingHigh silence — explicit gate:** ≥6 h after Chunk 5's pods rolled (one full mining duty cycle), query `ALERTS{alertname="CPUThrottlingHigh", namespace="web3", alertstate="firing"}`. Empty → **skip this step entirely.** Still firing → append a silence with matchers `alertname=CPUThrottlingHigh`, `namespace=web3`, `pod=xmrig-.*` (`matchType: "=~"` on the pod matcher only).
5. Housekeeping: add the missing `$schema` header to `kubernetes/apps/gpu-operator/gpu-operator/app/prometheusrule.yaml` (`# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/monitoring.coreos.com/prometheusrule_v1.json`).
6. Commit steps 2–5 together (`fix(observability): drop vmcluster rule group, silence structural memory overcommit`), push, reconcile.

#### Verification (E2E)
- [ ] **First:** `flux get hr rook-ceph-cluster -n rook-ceph` → Ready=True (postRenderers rendered cleanly; the 4 dependents keep reconciling)
- [ ] `CephPoolGrowthWarning` evaluates cleanly: vmalert `/api/v1/rules` shows the `pools` group healthy, no `lastError` (the patched expr was pre-verified against live vmsingle)
- [ ] AlertingRulesError + both TooManyLogs + both RequestErrorsToAPI resolve (allow ~30 min for log-rate windows to drain) — note they may already be green if executing after 2026-06-08 11:41 UTC (self-heal); the patch is still wanted for the next mgr roll
- [ ] `kubectl get vmrule -n observability` no longer contains vmcluster-group rules
- [ ] Silence exists — note the CRD collision: bare `kubectl get silence` resolves to the WRONG (cluster-scoped monitoring.giantswarm.io) CRD. Use: `kubectl get silences.observability.giantswarm.io -n observability kube-memory-overcommit-homelab`, and confirm KubeMemoryOvercommit shows silenced in Alertmanager
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` exits 0

#### Rollback
Step 1 is its own commit — revert it alone if the HR blocks. Steps 2–5 are independent reverts.

---

## Testing Strategy

- **Per-chunk:** as listed above (every chunk has cluster-observable E2E checks, not just YAML validation).
- **Repo validation before each push:** `bash .agents/skills/pr-review/scripts/validate-pr.sh` — **only valid after Chunk 3 step 3 lands** (the script exits 1 on every run today because `.yamllint.yaml` is missing).
- **End state (the actual acceptance criterion):** run **after at least one full inference-session window** (≥24 h after Chunk 4 lands — a point-in-time check between sessions false-passes the saturation trio):
  ```bash
  # via the observability MCP or port-forward vmalertmanager-victoria-metrics 9093
  curl -s 'http://localhost:9093/api/v2/alerts?active=true&silenced=false&inhibited=false' | jq '[.[].labels.alertname] | sort'
  # expected: ["Watchdog"]
  # plus the 24h proof for the session-dependent trio:
  # max_over_time(ALERTS{alertname=~"NodeSystemSaturation|NodeDiskIOSaturation|NodeCPUHighUsage"}[24h]) → empty
  ```
  (KubeHpaMaxedOut + KubeMemoryOvercommit firing-but-silenced is the intended end state.)
- **Backup restoration confidence (post Chunk 1):** after the first full sync cycle, confirm new kopia snapshots exist (`task volsync:stats`).

## Dependencies & Sequencing Notes

- Chunk 1 is the only urgent one (backups down since 06-06). Land it first, alone, in its own PR if preferred.
- **Hard orderings:** Chunk 6 after Chunk 1 is verified (shared file + snapshot-backlog IO herd). Chunk 4 after Chunks 5+6 (control-1 headroom; pre-flight gate). Chunk 7 step 4 gated on Chunk 5's outcome (+6 h observation).
- The victoria-metrics cascade (Chunk 7 steps 1–2) self-heals ~2026-06-08 11:41 UTC — alerts may already be green at execution time; the patch is still wanted to prevent recurrence on the next mgr roll.
- Nothing here interacts with the open `feat/gpu-operator-oci` branch.

## Rollback Plan

All repo changes are independent single-purpose commits — revert individually, `task reconcile`. Cluster ops in Chunk 3 delete only verified-orphaned objects (helm-storage pre-check guarantees no uninstall side effects). No migrations, no data mutations. The rook rule fix ships as a chart-native `prometheusRuleOverrides` value (the simplify pass removed the originally planned positional postRenderers patch), so the blocked-HelmRelease failure mode is gone; an immediate post-merge Ready check on rook-ceph-cluster remains as a guard.

---

## Review Results (automated, 2026-06-07)

Multi-dimension review (6 reviewers; every CRIT/HIGH finding adversarially re-verified against the live cluster and pulled chart sources before counting).

| Severity | Found | Auto-fixed | Notes |
|---|---|---|---|
| CRIT | 1 (reported by 3 dimensions) | **[FIXED]** | llama-server 4cpu/20Gi unschedulable on control-1 + Recreate strategy → outage. Fixed: 2cpu/16Gi, moved to Wave 3 behind Chunks 5+6, mandatory pre-flight check added. |
| HIGH | 4 | **[FIXED]** | (1) CSI trim targeted values removed from rook chart in v1.20.0 → rewritten against `ceph-csi-drivers` chart with complete per-container maps + conflict-matrix update. (2) Overcommit math omitted Chunk 4's added cores → restated honestly in D4-A (margin ~1.5–2 cores, gate after Chunks 1+4+5+6). (3) Chunk 1 verification selector matched zero pods → corrected to `app=...-ctrlplugin`. (4) `validate-pr.sh` exits 1 always (missing `.yamllint.yaml`) → housekeeping step added to Chunk 3. |
| MED | 10 | **[FIXED]** | Chunk 1 snippet made additive-only; CNPG switchover expectations corrected (2×); postRenderers blast radius + index pre-check + own-commit; Chunk 7 step-4 explicit gate; Chunk 4 non-blocking verification flow; gatus scope resolved to all 7 vmcps; decisions-confirmed state line; silence CRD collision in verification; embedding-quiescence check added; end-state acceptance time-qualified. |
| LOW | 12 | **[FIXED]** (11) / noted (1) | Quoting/style conventions, schema headers, models.ini rationale comment, memory-file absolute path, helm-storage pre-check, admission-secret made deterministic, kustomization name resolved, IO-herd ordering made mandatory, session-TTL check. Open: the ~23:35/~11:53 inference-session trigger remains untraced (follow-up). |

Reviewer-invalidated findings were discarded. Remaining open items (non-blocking): inference-session trigger origin, cadvisor double-scrape suspicion (dashboards ~2× CPU), upstream toolhive issue filing (cross-session optimizer caching). D7 was resolved 2026-06-07: optimizer in active use → keep and tune (D7-B); Chunk 2 was rewritten accordingly (the review's embedding-quiescence check was replaced by fast-embed + optimizer-functional checks).

---

## Post-Merge Verification Checklist (the remaining work)

Run after the branch merges to `main` and Flux reconciles:

1. **First:** `flux get hr rook-ceph-cluster -n rook-ceph` → Ready=True (4 dependent Kustomizations unaffected)
2. RBD ctrlplugin pods (`-l app=rook-ceph.rbd.csi.ceph.com-ctrlplugin`) list `csi-snapshotter` **with the trimmed requests**; watch `ceph health` + control-1 load while the 18-snapshot backlog drains; all `kubectl get replicationsources -A` show fresh LAST SYNC; `count(volsync_volume_out_of_sync == 1)` → 0; spot-check one kopia mover `SUCCESS`
3. TEI pod rolled with `--max-client-batch-size 256`; vmcp-unified initialize answers <10 s **with optimizer meta-tools intact**; `claude mcp list` → unified ✓; gatus `ai` group green; dragonfly session keys bounded after ~1 h
4. xmrig throttle ratio < 0.25 (CPUThrottlingHigh ×3 resolves); hashrate steady; hugepages fit
5. CNPG rolls with ONE planned switchover → "Cluster in healthy state", 3/3 ready, streaming reattach (no pg_basebackup)
6. llama-server pod Running on control-1, model loads with no-mmap, one real inference OK, `kubectl top node` control-1 memory ≤ ~85% (fallback: cache-ram 4096 → 2048)
7. `sum(namespace_cpu:kube_pod_container_resource_requests:sum)` ≈ 20–21 < 22 → KubeCPUOvercommit resolves (gate needs ALL chunks reconciled; note llama cpu stayed at 1, so the margin is ~1 core better than planned)
8. Silence active: `kubectl get silences.observability.giantswarm.io -n observability kube-memory-overcommit-homelab` (bare `kubectl get silence` resolves the WRONG cluster-scoped CRD); KubeMemoryOvercommit shows silenced in Alertmanager
9. vmalert `pools` group healthy (no lastError); AlertingRulesError / TooManyLogs ×2 / RequestErrorsToAPI ×2 resolve within ~30 min (may already be green after the 2026-06-08 11:41 UTC self-heal — the patch is for the next mgr roll)
10. **≥6 h after deploy:** CPUThrottlingHigh gate — `ALERTS{alertname="CPUThrottlingHigh", namespace="web3", alertstate="firing"}` empty → done; still firing → add the xmrig silence (Chunk 7 step 4)
11. **≥24 h after deploy** (one full inference window, sessions ~23:35/~11:53): `max_over_time(ALERTS{alertname=~"NodeSystemSaturation|NodeDiskIOSaturation|NodeCPUHighUsage"}[24h])` → empty; end-state Alertmanager query returns `["Watchdog"]` unsilenced; then update the memory file `~/.claude/projects/-home-tanguille-Documents-PriveProjecten-cluster/memory/project_llama_server_gemma_moe_design.md` (mmap guidance was ik_llama-era)
12. Cleanup: delete `/tmp/kubeconfig-fix-alerts`; HelmReleaseReconciliationFailure + duplicate KubeHpaMaxedOut should already be clear from the Chunk 3 cluster ops

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.
- Line numbers in this plan are hints from 2026-06-07; the named YAML key/section is authoritative — locate by key, not line.

**Important**: Every prompt should verify the branch and worktree before doing any work.

Suggested continuation prompt per chunk:
> Verify branch `fix/cluster-alerts` and worktree. Continue `docs/plans/fix-open-alerts.md`: Decisions D1–D7 are confirmed as marked — do not re-ask. Mark Chunk N complete with verification evidence, then execute the next chunk per the Wave order and its dependencies, following its steps exactly. Run `bash .agents/skills/pr-review/scripts/validate-pr.sh` before committing.
