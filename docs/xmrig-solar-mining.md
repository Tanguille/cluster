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

`kubernetes/apps/web3/monero/guard/resources/controller.py` polls VictoriaMetrics every 30s
and exports `xmrig_guard_safe{node}` plus `xmrig_guard_rank{node}`. It treats telemetry as
untrusted: a complete set of fresh samples is required before a node can be safe, and every
failure path fails closed. Policy is code, so changing a threshold requires a reviewed diff.

`SENSORS` decides which source gates which node. control-1 is a VM with no visible NVMe, so it
is gated on CPU headroom; the bare-metal mini PCs are gated on NVMe Composite temperature.

| node | source | recover | trip | recovery dwell | trip dwell | panic |
|---|---|---|---|---|---|---|
| control-1 | non-xmrig CPU % | 50 | 70 | 600s | 120s | — |
| control-2 | NVMe Composite C | 62 | 65 | 180s | 60s | 67C |
| control-3 | NVMe Composite C | 62 | 65 | 180s | 60s | 67C |

Only `temp1` (Composite) is read. `temp2`-`temp4` are internal die sensors running ~9C hotter
with no comparable rating, so including them gated a Composite threshold against the wrong value.

The CPU gate has no panic limit: a busy CPU carries no equivalent of a drive's absolute rating.

### Timing budget

The 65C trip is sized against the 70C drive rating. Across 1151 miner starts over 180d the rise
is p50 0.88 / p90 1.13 / p99 **1.35** C/min, so the 1.1C/min this was originally sized on is the
p90, not a worst case:

| leg | seconds |
|---|---|
| evaluation interval (sample the crossing) | 30 |
| trip dwell | 60 |
| KEDA `pollingInterval` | 30 |
| drain (`terminationGracePeriodSeconds`) | 15 |
| **total** | **135** |

135s is 3.0C of rise at the p99 rate, so the trip path alone would peak near 68.0C. It does not
get there: the 67C panic trip skips the dwell entirely, so a ramp that outruns the dwell sheds at
67 rather than riding the full 135s.

**Measured, this is tighter than the model.** Real mining bursts under these parameters peaked at
p50 67.8 / p90 68.8 / **max 69.8C** against the 70C rating. There is 0.2C of margin. Nothing here
may be loosened, and raising `trip` is where all the cost sits: 65→66 multiplies hours-spent-safe-
above-65C by 4-6x to buy +3-5pp of safe time.

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

The original commissioning replay (7d, `recover 60/trip 64/600s/120s` → `62/65/300s/60s` + 68C
panic) put delivered replica-hours at 65.4h → 84.2h, +29%. **Its magnitudes are superseded** —
see the re-audit below, which found that window predated both the `temp1` sensor fix and
enforcement, so every percentage from it is measured against a reading ~9C hotter than Composite.

The structural caveat still holds: any replay is an upper bound, because it applies a more
permissive policy to temperatures recorded under a restrictive one, and more mining means more
heat. It validated the harness, not the counterfactual.

### Re-audit, 2026-08-06

Replayed against 180d of `node_hwmon_temp_celsius` (VM retention) at the native 20s cadence.
Downsampling to 60s hides 9C spikes and inflates safe-time by ~10pp, so do not replay at 60s.

Only **2026-07-27 14:02 onward** reflects the deployed policy: before that the guard read all
sensors rather than `temp1`, gating on a value ~9C hotter, and 2026-07-17→21 it emitted without
enforcing. Every safe-percentage figure recorded before this audit — in the Measured section
above and in the `controller.py` comments — came from those windows and does not reproduce on
either sensor set. The direction holds everywhere; the magnitudes were stale.

The guard works: **zero samples ≥70C occurred while `safe=1`.** Before it existed, ungated mining
put control-3 over its rating 51% of the time it mined, peaking at 86C.

Tuning outcome: the existing values are near-optimal. Two free changes applied — recovery dwell
300→180s (+0.8pp/+1.1pp over 30d, no change in exposure above 65C) and panic 68→67C (0.0pp cost,
caps the hottest sample called safe at 66.8C). `recover 62`, `trip 65` and `trip_dwell 60` all
held against a 612-candidate grid.

**The binding constraint is one drive, not a threshold.** The `nvme1` Micron 7450 480GB runs ~10C
hotter than the 980 PRO beside it and sets the gate on both nodes (control-2 p90 70C vs 58C;
control-3 p90 73C vs 64C). During surplus hours the idle baseline is already 63-64C against a
workload that adds 8.5C in 7 minutes, which is why 68-72% of free solar watts go unmined.
Cooling that drive converts directly into safe-time; no threshold change can.

## Alerts

| alert | fires when |
|---|---|
| `XmrigGuardEnforcementBypassed` | a miner runs while its node's gate should be shut (KEDA down, HPA wedged, manual scale). Mirrors KEDA's cardinality and freshness gate exactly, so a stale `safe=1` cannot suppress it. |
| `XmrigGuardThermalPanic` | a drive is above 67C with a miner still running: the zero-dwell fast path failed |
| `XmrigGuardAbsent` | guard signal missing or wrong cardinality (gate closed, mining silently off) |
| `XmrigGuardLatchedUnsafe` | gated unsafe 6h while the drive stayed within the 62C recovery band, i.e. latched rather than hot |
| `XmrigGuardEvaluationErrors` | >0.01/s evaluation failures; affected nodes cannot re-earn `safe=1` |

## Known and accepted

- **A full node forfeits its slot.** The hard `nodeSelector` means a miner cannot shop for
  another node. control-2 and control-3 have 21.7Gi allocatable and sit near 99% memory
  requests, which cost 1.69 replica-hours in the first 24h. `preemptionPolicy: Never` on
  `low-priority-mining` means the miner waits rather than evicting real work, which is correct.
- **The miner runs without hugepages.** The 2368Mi static reservation was withheld from every
  node's allocatable memory even at 0 replicas, so it was dropped. The reclaim is 2368Mi while
  idle but only ~320Mi while mining, since xmrig then requests 2Gi of regular pages instead.
  RandomX loses hashrate without hugepages; that is the accepted price for giving the two 29GB
  nodes their memory back for the ~94% of the time no miner is running.
  The request is deliberately under the ~2336Mi RandomX actually uses: post-reclaim headroom is
  2389Mi/2373Mi on control-2/3, and requesting the true figure would park the miner Pending
  forever behind `preemptionPolicy: Never`. The overshoot bursts against the 3Gi limit.
- **The CPU path gets a 180s freshness budget, the NVMe path keeps 120s.** cAdvisor scrapes at
  60s where the other six sources scrape at 20s, and `query_cpu` dates the observation by `min()`
  across all of them. At 120s control-1 ran an age p99 of 102s against a 120s ceiling and
  self-invalidated 465 times in 10.26d (45 and 49 on the NVMe nodes), each latching a 600s
  recovery dwell. It was self-reinforcing: 1.7 errors/h idle against 4.8/h with a miner present,
  because the cAdvisor join only engages when one is. 85 of control-1's 118 mining bursts died
  under 5 minutes to this, not to its thresholds, which replay at 93.8% safe against 75.8% live.
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
