# xmrig per-node thermal gating

**Branch**: `feat/xmrig-per-node-gating`
**Worktree**: `.claude/worktrees/xmrig-per-node-gating`

## Problem

The KEDA trigger gates all mining on `min(xmrig_guard_safe)` across all three nodes, so one
hot NVMe stops the whole fleet. Measured over 7d (2026-07-23 → 07-30):

| metric | value |
|---|---|
| solar available (>=25W export) | 47.5h |
| desired replica-hours | 137 |
| delivered replica-hours | 8.7 (6%) |
| gate open | 25% of wall time |
| per-node safe fraction | control-1 96%, control-2 60%, control-3 30% |
| solar hours lost per node | c1 3.8h, c2 29.1h, c3 40.0h |
| NVMe above the 64C trip during solar hours | c2 28.9h, c3 35.3h |

Before the guard went enforcing (~2026-07-14) capture was 103-107%, so the solar half of the
system is fine. The drives now idle in the 60-64C band all year (5-week weekly averages 59.9,
61.3, 64.3, 63.7, 62.4 C), i.e. on top of both thresholds, which is why the gate is a coin flip.

Modelled replica-hours for last week's actual solar and guard data (hysteresis-free upper
bound; the same model reproduces today's design at 8.6 vs 8.7 actual):

| design | replica-h/7d |
|---|---|
| today | 8.7 |
| per-node split, trip 64C | 73 |
| + trip 65C | 82 |
| + trip 66C | 90 |

The split is the 8x. Threshold tuning is worth roughly 5h per node per degree on top.

## Why one Deployment cannot do this

The HPA sheds an arbitrary pod, so nothing guarantees the pod on the node that just tripped is
the one that dies. Making placement follow the guard needs the guard to write node labels or
taints: RBAC on a pod that deliberately runs with `automountServiceAccountToken: false`, plus
`NoExecute` semantics on a control-plane node, plus new guard code. Per-node autonomy in
Kubernetes is spelled "one workload per node". Rejected alternatives: descheduler (new
component, unbounded eviction latency), `IgnoredDuringExecution` affinity on a guard-written
label (does not evict the pod on the node that just went hot, so it is unsound on the safety
path), priority/preemption (nothing to preempt with).

Three copies are expressed as a flux-operator `ResourceSet` (already installed on the cluster,
first use in this repo) rather than three files or three kustomize overlays:

| approach | lines | delta vs 170 today |
|---|---|---|
| naive 3x copy | ~510 | +340 |
| kustomize overlays (base + 3) | ~250 | +80 |
| ResourceSet | ~180 | ~+10 |

A kustomize *component* is the wrong tool: it injects resources into a kustomization, it does
not instantiate N copies of a base.

## Step 1 — the split, thresholds unchanged

Delivers the 8x on its own with no change to drive safety. Guard thresholds stay at trip 64C /
recover 60C.

- `kubernetes/apps/web3/monero/xmrig/resourceset.yaml` replaces `helmrelease.yaml` and
  `scaledobject.yaml`. Inputs are the three nodes plus a staggered activation threshold.
- Each trigger subtracts 50W per *safe* node that outranks it and takes what is left, with a
  uniform 25W activation. Without that subtraction, three ScaledObjects reading one global
  export figure would all start at 50W. Unlike a fixed activation threshold it does not
  reserve watts for a node that is unavailable: if control-1 is CPU-saturated, control-2
  takes the first slot rather than waiting for 75W.
- Ranking is the guard's `xmrig_guard_rank`, ordered by measured thermal availability
  (c1 96% → c3 30%), which inverts the old "prefer control-2/control-3, more power efficient"
  preference. It lives in the guard's `PRIORITY` tuple, so re-prioritising needs no manifest
  edit, and a future headroom-derived ranking replaces that tuple alone.
- Measured worth of dynamic over the static 25/75/125 stagger it replaced: 0.02 replica-hours
  over 7d, 0.6 over 30d. The reason it is small is that export here is bimodal (44.6h of the
  47.5h above 25W last week sat at the 150W cap), so the ranking rarely binds. It was adopted
  for removing three hardcoded numbers that had to stay consistent with `threshold: 50`, not
  for the throughput.
- Each HelmRelease pins its Deployment to one node with a `nodeSelector`.
  `podAntiAffinity` goes away: one replica per node is now structural.
- The gate query per node collapses from the `min() x count()==3 x freshness` cardinality dance
  to the same shape against a single series.
- `kubernetes/apps/web3/monero/xmrig/service.yaml` keeps one stable `xmrig` Service across the
  three Deployments, selecting the existing `app.kubernetes.io/component: thermal-guarded` pod
  label, so the dashboard's `http://xmrig.web3.svc.cluster.local:42000` keeps working.
- `XmrigGuardEnforcementBypassed` becomes per-node, joining guard verdict to the matching
  Deployment by node. Its old global `min()` form would false-fire constantly once one node
  can mine while another is unsafe.

Known accepted behaviours:

- If the guard's rank series is missing for a node, that node's `scalar()` yields NaN, every
  rank comparison is false, and it behaves as top priority. That fails open on *allocation*
  only (worst case one extra miner, 50W over-drawn); the safety gate is a separate factor in
  the same query and still fails closed.
- The dashboard polls one Service and gets whichever pod answers, so it under-reports total
  hashrate when more than one miner runs. Pre-existing, unchanged by this step.
- With `maxReplicaCount: 1` the HPA `behavior` block is probably inert (0->1 and 1->0 are KEDA
  activation transitions, not HPA scaling). It is kept unchanged rather than deleted on a safety
  path; revisit once step 1 measurement confirms real drain latency.

**Measure for ~5 days**: delivered replica-hours vs the 73h model; per-node safe fractions;
no `XmrigGuardEnforcementBypassed` false-fires; force a single-node trip and confirm only that
node drains, inside the timing budget.

**Renovate coverage: verified, no config change needed.** A local `--dry-run=extract` lists
`resourceset.yaml` as a packageFile carrying `djerfy/xmrig` at the pinned digest, detected
twice by `helm-values` and `kubernetes` exactly as the `.renovaterc.json5` comment describes
for plain HelmReleases.

## Step 2 — response-time levers

No threshold change. Buys back the time that step 3 spends.

- guard `EVALUATION_INTERVAL_SECONDS` 60 -> 30 (node-exporter scrapes at 20s, verified, so this
  is real time and not just polling a stale sample)
- trip dwell 120s -> 60s
- KEDA `pollingInterval` 60 -> 30 on all three ScaledObjects
- new zero-dwell panic trip at 68C in `DwellPolicy.observe()`, plus a critical alert for
  `nvme_temp > 68 and replicas > 0` (the panic line crossed with miners still running means the
  fast path failed)
- `HTTP_TIMEOUT_SECONDS` 10 -> 5, and a deadline-based sleep. One evaluation now issues 13
  queries (7 for control-1, 3 per NVMe node), so a hung VictoriaMetrics blocks up to 130s of
  serial wait against a 120s freshness budget: the guard fails closed on every node plus a
  stale scrape, later and more expensively than a faster timeout would. The loop also sleeps
  a flat 60s *after* the work, so the true period drifts by the evaluation duration;
  `time.sleep(max(0, deadline - time.monotonic()))` fixes both. Two lines, no new concepts.

**Measure**: observed trip-to-drain latency on a real or forced trip, target < 2.5 min.

## Step 3 — thresholds

- trip 64C -> 65C. Chain at 65C: scrape + 30s eval + 60s dwell + 30s poll + drain ~= 3.5 min =
  3.85C at the observed 1.1C/min, peak ~68.9C against the 70C rating: the same ~1.1C margin the
  64C design has today. 66C is defensible on the same math but leaves ~0.9C; take it only if
  step 2's measured latency beats the budget.
- recovery 60C -> 62C, recovery dwell 600s -> 300s. Recovers the ~5h/week currently spent
  latched closed while the drive is already cool, and the ~11h/week stuck in the 60-64C dead
  band. Note the code comment claiming "idle Composite never exceeded 62C over 7d" is stale:
  the drives now idle above 64C on hot afternoons. This does not unlock summer afternoons, and
  no threshold can - it unlocks mornings and winter.
- Anti-flap at trip 65 / recover 62: the miner must climb 3C at <=1.1C/min (>=2.7 min) plus the
  60s dwell, then cool back to 62C plus 300s dwell. Full cycle >= 12-15 min. That is duty
  cycling sized by the drive's thermal mass, not flapping.
- `XmrigGuardLatchedUnsafe` hardcodes 60C twice; update to 62.
- Rewrite the timing-budget comments in `scaledobject.yaml` and `controller.py`. Every number in
  them changes and they are the design record.

**Measure for a week**: weekly max Composite stays < 70C; cycle period >= 10 min; latched-cool
hours ~= 0; transition count; control-3 hugepage allocation health under repeated 2368Mi
alloc/free on a node that runs chronically near 95% memory; no NPD XFS/NVMe conditions.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been
  consolidated into existing documentation, the plan file can be removed. If there is no
  relevant existing documentation, the plan should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
