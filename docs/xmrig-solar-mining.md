# xmrig solar-gated mining

Monero mining that runs only on exported solar power, and only on nodes whose hardware has
headroom. One miner per control-plane node, each gated independently.

## Shape

`kubernetes/apps/web3/monero/xmrig/resourceset.yaml` is a flux-operator `ResourceSet`
templated over three node inputs. Each input produces one HelmRelease (a Deployment pinned
with `nodeSelector`) and one ScaledObject.

One Deployment with three replicas cannot express this: the HPA sheds an arbitrary pod, so
nothing guarantees the pod on the node that just tripped is the one that dies. Per-node
autonomy in Kubernetes is spelled "one workload per node". Rejected alternatives: guard-written
node labels or taints (needs RBAC on a pod that deliberately runs with
`automountServiceAccountToken: false`, plus `NoExecute` semantics on a control-plane node),
descheduler (new component, unbounded eviction latency), priority/preemption (nothing to
preempt with).

`service.yaml` keeps one stable `xmrig` Service across the three Deployments, selecting
`app.kubernetes.io/component: thermal-guarded`, so the dashboard's
`http://xmrig.web3.svc.cluster.local:42000` resolves regardless of which node is mining.

## The guard

`kubernetes/apps/web3/xmrig-guard/app/resources/controller.py` polls VictoriaMetrics every 30s
and exports `xmrig_guard_safe{node}` plus `xmrig_guard_rank{node}`. It treats telemetry as
untrusted: a complete set of fresh samples is required before a node can be safe, and every
failure path fails closed. Policy is code, so changing a threshold requires a reviewed diff.

`SENSORS` decides which source gates which node. control-1 is a VM with no visible NVMe, so it
is gated on CPU headroom; the bare-metal mini PCs are gated on NVMe Composite temperature.

| node | source | recover | trip | recovery dwell | trip dwell | panic |
|---|---|---|---|---|---|---|
| control-1 | non-xmrig CPU % | 50 | 70 | 600s | 120s | — |
| control-2 | NVMe Composite C | 62 | 65 | 300s | 60s | 68C |
| control-3 | NVMe Composite C | 62 | 65 | 300s | 60s | 68C |

Only `temp1` (Composite) is read. `temp2`-`temp4` are internal die sensors running ~9C hotter
with no comparable rating, so including them gated a Composite threshold against the wrong value.

The CPU gate has no panic limit: a busy CPU carries no equivalent of a drive's absolute rating.

### Timing budget

The 65C trip is sized against the 70C drive rating and a measured ~1.1C/min rise under load:

| leg | seconds |
|---|---|
| evaluation interval (sample the crossing) | 30 |
| trip dwell | 60 |
| KEDA `pollingInterval` | 30 |
| drain (`terminationGracePeriodSeconds`) | 15 |
| **total** | **135** |

135s is 2.5C of rise, peaking near 67.5C, leaving 2.5C of margin. The 68C panic trip skips the
dwell entirely (75s, ~69.4C) and bounds the case where the ramp beats the dwell.

Changing any leg invalidates the trip threshold. They move together.

## Allocation

Each ScaledObject reads its own node's verdict and takes only the export left after every
*safe* node that outranks it has taken its 50W. Without that subtraction, three ScaledObjects
reading one global export figure would all activate at 50W.

Ranking is `xmrig_guard_rank`, derived from the `PRIORITY` tuple in the controller, so
re-prioritising needs no manifest edit. Order follows measured availability: over 7d,
control-1 97.8%, control-2 44.7%, control-3 31.5% (pre-step-3 numbers).

Unlike a fixed activation threshold, the subtraction skips an unavailable node instead of
reserving watts for it: if control-1 is CPU-saturated, control-2 takes the first slot rather
than waiting for 75W. Measured worth of dynamic over the static 25/75/125 stagger it replaced
is small (0.6 replica-hours over 30d) because export here is bimodal; it was adopted for
removing three hardcoded numbers that had to stay consistent with `threshold: 50`.

The gate itself is a separate factor in the same query: cardinality must be exactly 1 and the
sample must be under 120s old, so a duplicate or stale `safe=1` closes the gate.

## Measured

Per-node gating, first 24h after rollout (2026-07-31):

| metric | before | after |
|---|---|---|
| capture vs solar hours ≥25W | 6% | 51% |
| peak concurrent miners | 1 | 3 |

Replaying 7d of real telemetry through `DwellPolicy` (`recover 60/trip 64/600s/120s` →
`62/65/300s/60s` + 68C panic): 65.4h → 84.2h of delivered replica-hours, +29%. control-2 44.7%
→ 62.3% safe, control-3 31.5% → 40.4%.

That replay is an upper bound: it applies a more permissive policy to temperatures recorded
under the restrictive one, and more mining means more heat. It matched actuals closely on the
*current* policy (control-2 20.0% modelled vs 20.4% actual, control-3 8.5% vs 8.5%), which
validates the harness but not the counterfactual.

Threshold tuning is worth roughly 5h per node per degree. The per-node split was the 8x; the
thresholds are the next ~30%.

## Alerts

| alert | fires when |
|---|---|
| `XmrigGuardEnforcementBypassed` | a miner runs while its node's gate should be shut (KEDA down, HPA wedged, manual scale). Mirrors KEDA's cardinality and freshness gate exactly, so a stale `safe=1` cannot suppress it. |
| `XmrigGuardThermalPanic` | a drive is above 68C with a miner still running: the zero-dwell fast path failed |
| `XmrigGuardAbsent` | guard signal missing or wrong cardinality (gate closed, mining silently off) |
| `XmrigGuardLatchedUnsafe` | gated unsafe 6h while the drive stayed within the 62C recovery band, i.e. latched rather than hot |
| `XmrigGuardEvaluationErrors` | >0.01/s evaluation failures; affected nodes cannot re-earn `safe=1` |

## Known and accepted

- **A full node forfeits its slot.** The hard `nodeSelector` means a miner cannot shop for
  another node. control-2 and control-3 have 21.7Gi allocatable and sit near 99% memory
  requests, which cost 1.69 replica-hours in the first 24h. `preemptionPolicy: Never` on
  `low-priority-mining` means the miner waits rather than evicting real work, which is correct.
- **The miner needs all of a node's hugepages.** 2368Mi requested against 2368Mi allocatable,
  so exactly one miner fits per node. Any other hugepages consumer on a control node blocks a
  miner outright; the CNPG cluster carries an explicit cap for this reason.
- **The dashboard under-reports hashrate when more than one miner runs**, because it polls one
  Service and gets whichever pod answers.
- **A missing rank series fails open on allocation only.** `scalar()` yields NaN, every rank
  comparison is false, and the node behaves as top priority: worst case one extra miner, 50W
  over-drawn. The safety gate is a separate factor and still fails closed.
- **Summer afternoons stay shut.** The drives idle in the 60-64C band on hot days, on top of
  both thresholds. No threshold unlocks that; these thresholds unlock mornings and winter.

## Operating notes

- The guard's CPU path issues 7 serial queries against a 5s timeout. control-1 is the only node
  on that path, so evaluation errors concentrate there.
- A miner starting or stopping changes control-1's CPU sample count (host+presence, plus xmrig
  only while one runs). That is expected and re-baselines the source set; it must not be treated
  as tampering, which previously made mining on control-1 trip control-1.
- `xmrig_guard_safe` has one series per node. Range queries spanning a guard or
  kube-state-metrics restart return two partial series; use `max by (node)` in a subquery, and
  remember gaps are not zeros (`or vector(0)` where absence means zero).
- kube-state-metrics scrapes at 20s, not 60s. `sum_over_time(...)/60` overcounts replica-hours
  3x; prefer `avg_over_time(...) * hours`.
