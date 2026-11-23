# Pearl solar mining on the 2070

`web3/pearl-miner` runs a CUDA Pearl (PRL) miner on the control-1 RTX 2070 only when there is solar
surplus or the tech cave is cold. It is a Deployment at 0 replicas; a KEDA ScaledObject (`kubernetes/apps/web3/pearl-miner/app/scaledobject.yaml`)
raises it to 1. Pearl outranks xmrig: the gate counts running xmrig draw as available, and xmrig sheds
once the miner's draw shows on the meter. On UPS load xmrig stops first (80 %, Pearl at 85 %).
Pool `fr.pearl.herominers.com:1200`, worker `cluster-2070`, payout address in `pearl-miner-secret`. The image is built weekly by the `Tanguille/CPPminer` fork (`cluster-image` branch) from upstream's `cuda` branch
(`main` and the `vX.Y` tags lack the tensor-core kernels: 13 vs 46 TMAC/s on the 2070) with the 1% dev fee disabled (MIT),
pushed as `ghcr.io/tanguille/cppminer:<commit date YYYYMMDD.HHMM>`; Renovate bumps the tag.

## Gate

One query returning 0 or 1, evaluated every 30 s, keyed on the running replica count so start and
keep levels differ. A missing series gives 0, and a scaler error falls back to 0 replicas.

| factor | start | keep |
| --- | --- | --- |
| surplus (15 m net export, miner and xmrig draw added back), or | 200 W | 100 W |
| tech cave temperature, 15 m average | under 18 C | under 18 C |
| UPS load max, 5 m | under 65 % (85 % stop minus its ~15 point step and a margin) | under 85 % |
| 2070 free VRAM | 2560 MiB | 1024 MiB |
| 2070 temperature | under 80 C | under 80 C |
| R9700 junction, 5 m max | under 85 C | under 90 C |
| control-1 guard (`xmrig_guard_safe`) | safe and under 120 s old | same |
| control-1 memory | 64Mi request fits, 2Gi `MemAvailable` | same |

DCGM is disabled in the cluster, so the gate uses `nvidia_smi_*`. A cold room replaces only the
surplus test (the heat is wanted, so grid power is fine); every other factor still applies, and a
missing room sensor counts as warm. The room was under 18 C 21 % of the time over the 30 days to
2026-11-19 (range 9.7 to 23.4 C).

## Expected behaviour

Measured offline on the 2070 with `--mock`: 44 to 47 TH/s at about 168 W, 70 C, 1.6 GiB VRAM.
d3-nano latency goes from about 75 ms to 175 ms while mining (accepted). A 14-day replay of the
surplus-only gate (2026-11-05 to 2026-11-18, 30 s steps) opens about 0.33 h/day with 19 starts and 7
cycles in one window, so it can flap. The replay predates Pearl-first priority (it still reserved
xmrig slots) and the cold-room path, which adds up to the
21 % of the time the room is under 18 C (still capped by the UPS start rule); its 15 m average
crossed 18 C 35 times in those 30 days. Power alone allows about 1.4 h/day; the UPS start rule is the binding
constraint. Add a minimum off time if the trial shows flapping. The UPS rating (750 W) is an
assumption. xmrig gives way whenever the miner runs, and the R9700 balanced profile
(`amdgpu-undervolt`) loses time while the miner runs. Revenue is unmeasured.

## Alerts

`PearlMinerEnforcementBypassed` fires when the miner runs while a safety factor is unmet.
`PearlMinerGpuHot` fires above 85 C. `TechCaveSensorAbsent` fires when the room sensor has been
missing for 30 min, since the gate then reads the room as warm. The egress policy `pearl-miner-egress` allows kube-dns and the
public internet only (private ranges excluded, so no LAN), so a compromised miner cannot reach other `web3` pods.

## Stop everything

Flux drops a manually added annotation on its next reconcile, so suspend the Kustomization first.

```sh
flux suspend kustomization pearl-miner -n web3
kubectl -n web3 annotate scaledobject pearl-miner autoscaling.keda.sh/paused-replicas=0 --overwrite
# resume
kubectl -n web3 annotate scaledobject pearl-miner autoscaling.keda.sh/paused-replicas-
flux resume kustomization pearl-miner -n web3
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

## RTX 2070 constant-64 comparison

The optional [control](../kubernetes/apps/web3/pearl-miner/trial/control/kustomization.yaml)
and [constant-64](../kubernetes/apps/web3/pearl-miner/trial/constant64/kustomization.yaml)
overlays run the same [SM75 image](https://github.com/Tanguille/CPPminer/actions/runs/45275354432)
on the existing miner. Both enable local proof verification. Only `CP_CUDA_CONST64`
differs: `0` uses zero-B, `1` uses constant-64. This test image retains upstream fee
handling. The normal Flux path is still `pearl-miner/app`.

For a trial, wait for a normal KEDA window and point only `Kustomization/web3/pearl-miner`
at the chosen overlay in a pinned test source. Do not scale the Deployment. Confirm the
running image digest, `sm_75` packed kernel, and `constant-64 job B cached` or
`zero-B job B cached` in the pod log. A `verify OK` is local; count a share as accepted
only after a successful pool `submit response`. Compare work and `nvidia_smi_power_draw_watts`
over the same warmed-up intervals, and note any change in GPU tenant load. A short or
interrupted window can establish compatibility without establishing an efficiency gain.
After the bounded trial, restore the original Flux source and `pearl-miner/app` path
and verify the stock image, even if no share was found.
