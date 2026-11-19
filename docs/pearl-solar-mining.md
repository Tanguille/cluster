# Pearl solar mining on the 2070

`ai/pearl-miner` runs a CUDA Pearl (PRL) miner on the control-1 RTX 2070 only when there is solar
surplus or the tech cave is cold. It is a Deployment at 0 replicas; a KEDA ScaledObject (`kubernetes/apps/ai/pearl-miner/app/scaledobject.yaml`)
raises it to 1. xmrig keeps priority: the gate subtracts every safe xmrig slot from the surplus.
Pool `fr.pearl.herominers.com:1200`, worker `cluster-2070`, payout address in `pearl-miner-secret`. The image is built from the `Tanguille/CPPminer` fork (`ci/cluster-image`,
keeps the 1% dev fee).

## Gate

One query returning 0 or 1, evaluated every 30 s, keyed on the running replica count so start and
keep levels differ. A missing series gives 0, and a scaler error falls back to 0 replicas.

| factor | start | keep |
| --- | --- | --- |
| surplus (15 m net export, miner and xmrig draw added back, safe xmrig slots subtracted), or | 200 W | 100 W |
| tech cave temperature, 15 m average | under 18 C | under 18 C |
| UPS load max | 15 m under 70 % | 5 m under 85 % |
| 2070 free VRAM | 2560 MiB | 1024 MiB |
| 2070 temperature | under 80 C | under 80 C |
| R9700 junction, 5 m max | under 85 C | under 90 C |
| control-1 guard (`xmrig_guard_safe`) | safe and under 120 s old | same |
| control-1 memory | 1Gi request fits, 2Gi `MemAvailable` | same |

DCGM is disabled in the cluster, so the gate uses `nvidia_smi_*`. A cold room replaces only the
surplus test (the heat is wanted, so grid power is fine); every other factor still applies, and a
missing room sensor counts as warm. The room was under 18 C 21 % of the time over the 30 days to
2026-11-19 (range 9.7 to 23.4 C).

## Expected behaviour

Measured offline on the 2070 with `--mock`: 44 to 47 TH/s at about 168 W, 70 C, 1.6 GiB VRAM.
d3-nano latency goes from about 75 ms to 175 ms while mining (accepted). A 14-day replay of the
surplus-only gate (2026-11-05 to 2026-11-18, 30 s steps) opens about 0.33 h/day with 19 starts and 7
cycles in one window, so it can flap. The replay predates the cold-room path, which adds up to the
21 % of the time the room is under 18 C (still capped by the UPS start rule); its 15 m average
crossed 18 C 35 times in those 30 days. Power alone allows about 1.4 h/day; the UPS start rule is the binding
constraint. Add a minimum off time if the trial shows flapping. The UPS rating (750 W) is an
assumption. xmrig loses about 0.28 replica-hours in 14 days, and the R9700 balanced profile
(`amdgpu-undervolt`) loses time while the miner runs. Revenue is unmeasured.

## Alerts

`PearlMinerEnforcementBypassed` fires when the miner runs while a safety factor is unmet.
`PearlMinerGpuHot` fires above 85 C. `TechCaveSensorAbsent` fires when the room sensor has been
missing for 30 min, since the gate then reads the room as warm. The egress policy `pearl-miner-egress` allows kube-dns and the
public internet only (private ranges excluded, so no LAN), so a compromised miner cannot reach other `ai` pods.

## Stop everything

Flux drops a manually added annotation on its next reconcile, so suspend the Kustomization first.

```sh
flux suspend kustomization pearl-miner -n ai
kubectl -n ai annotate scaledobject pearl-miner autoscaling.keda.sh/paused-replicas=0 --overwrite
# resume
kubectl -n ai annotate scaledobject pearl-miner autoscaling.keda.sh/paused-replicas-
flux resume kustomization pearl-miner -n ai
```

To remove it for good, revert the commit; Flux prunes it.

## Trial (owner-run)

1. Merge and reconcile; the Deployment stays 0/0 while the gate is shut.
2. On a sunny day check: the gate opens, the pod schedules on control-1, shares are accepted and the
   worker shows at the pool, tenants stay Running, and the pool connection works under the egress
   policy.
3. Measure miner CPU and RSS (the requests are guesses), board power, temperatures, UPS load and
   starts per day.
4. Pause with the annotation above and time how fast the pod goes.

Go if no tenant restarts, no `NvidiaVramLow`, R9700 junction under 90 C, UPS under 90 % and no
control-1 reset while mining. Otherwise pause and revert.
