# R9700 undervolt deepening test plan — 2026-09

> **Closed 2026-10-28.** -100mV is live (`c8eb817c3`): bit-identical to -82 on
> the quality gate at +5.7% GFX clock for the same 242W. -120mV diverged on
> 11/60 greedy prompts with nothing in dmesg, so the plan ends at -100. Both
> were a 2026-10-27 quality-gate run, not the soak below. The engine is
> radiance since 2026-11-01 ([radiance-2026-11.md](radiance-2026-11.md)) and
> the decode latch is unseen there; re-check the premise before reusing this.
>
> Since 2026-11 the DaemonSet has two power caps instead of `POWER_LIMIT_WATTS`:
> `EFFICIENCY_WATTS` (default) and `BALANCED_WATTS` (solar surplus), re-asserted every 30 s.
> `VOLTAGE_OFFSET_MV` is unchanged and written once at start.

Runbook for pushing `VOLTAGE_OFFSET_MV` on the production R9700 (gfx1201,
`control-1`) past the -82mV it ran in 2026-09. Whoever runs it: follow
the steps in order, fill in the results table at the bottom, and stop at the
first failed gate.

Tuning is applied by the host DaemonSet
`kubernetes/apps/kube-system/amdgpu-undervolt.yaml` — read its inline comments
first; they hold the completed power and fan sweeps this plan builds on.

## Objective

Test whether a deeper core voltage offset reduces the heat-soak-driven GFX
clock decay suspected behind the vLLM "decode step latch" (ITL 31ms healthy →
90-200ms latched, cleared only by a pod restart, see `feat(llmkube): alert
when vllm decode latches at half speed`, commit 46de7f622).

This is **not** a throughput-ceiling test. The sweep in the DaemonSet comments
(sglang, uncontended) measured decode flat at 23.09/23.24/27.80/23.29 tok/s
across 210/250/290/330W — decode is memory-bandwidth-bound and does not move
with power or voltage. The only thing voltage can buy here is *how long the
card holds its clock at the fixed 250W cap before leakage at high die temp
eats the power budget*. Success is a slower/later clock decay, not a higher
tok/s number.

## Evidence this plan starts from (2026-09-06, live latch episode)

Snapshot `~/.local/state/vllm-itl-log/gpu-snapshots/latched_20260906T205132Z.txt`
(pod uptime 7630s, ITL 130.9ms, 4.25 running):

| metric | value |
|---|---|
| SOCKET_POWER | 249W (= configured PPT cap; THROTTLE_STATUS: THROTTLED) |
| GFX_VOLTAGE | 814mV |
| GFX clock | 2590MHz (was 3289MHz earlier in the same episode) |
| HOTSPOT / EDGE / MEM | 95 / 77 / 85 °C |
| VRAM used | 30177 / 32624 MB |

Per-minute rows from `itl_uptime.csv` after the GPU columns were added
(20:52Z onward, `awk -F, 'NF>=14{print $1,$3,int($4),$9,$10,$12}'`):

```text
ts                    uptime  itl  gfx_mhz  W    hot
2026-09-06T20:52:51Z  7722    113  3127     250  93
2026-09-06T20:53:51Z  7783    115  2497     249  92
2026-09-06T20:54:52Z  7843    168  3276     247  91
2026-09-06T20:55:52Z  7904    163  2959     249  92
2026-09-06T20:56:53Z  7964    102  3137     248  91
2026-09-06T20:57:53Z  8025    105  2534     251  92
2026-09-06T20:58:54Z  8085    134  3159     219  87
2026-09-06T20:59:54Z  8146    168  3093     250  90
```

**Caveat that gates the whole plan:** these are 1s instantaneous clock reads
and they oscillate 2497-3276MHz while ITL stays latched at 100-170ms. The
clock-decay hypothesis is *plausible* (fixed cap + heat soak → more leakage →
less clock per watt) but *not yet demonstrated* by this data, and there is no
healthy-state row with GPU columns to compare against — the columns were added
mid-latch. Step 0 exists to close that gap before any voltage is touched.

Community context, weight accordingly: one forum report of a stable -170mV on
an XFX RX 9070 XT (same silicon family, different board/cooler/bin). Single
anecdotal source. It says "possible", not "safe for this card".

## Rollout mechanics (read before changing anything)

- The DaemonSet script runs `apply_gpu_tuning` **once** at pod start, then
  `exec sleep infinity`. It does not watch the env. A changed
  `VOLTAGE_OFFSET_MV` takes effect only when the DaemonSet pod is recreated.
- Editing the env in the manifest changes the pod template → the DaemonSet's
  default RollingUpdate recreates the pod by itself. No manual pod delete
  needed. The new pod re-applies all four values (offset, power cap, fan
  target, acoustic rpm) — idempotent, the only thing that changes is `vo`.
- `power_dpm_force_performance_level=manual` is already set; writing a new
  `vo <mV>` + `c` to `pp_od_clk_voltage` on a running, loaded GPU is a live
  change. **The vLLM pod is not restarted and its traffic is not interrupted
  by a voltage change.** That also means the running pod's heat-soak state is
  preserved across the change — useful, see step 0.
- Scope: `nodeSelector: amd.com/gpu: "true"` matches exactly one node today
  (`kubectl get nodes -l amd.com/gpu=true` → `control-1` only). So "cluster-wide"
  and "single-node" are the same thing right now; no override mechanism is
  needed. If a second GPU node is ever labelled, this DaemonSet changes both
  and this plan must be re-scoped before use.
- Flux `cluster-apps` reconciles `./kubernetes/apps` on a 1h interval. A
  `kubectl set env` live patch is therefore reverted to whatever git says
  within ≤1h. Use that deliberately: git is the source of truth per step,
  `kubectl set env` is the seconds-fast emergency lever.

Apply a step (one-line PR, merge, wait for Flux or `flux reconcile ks
cluster-apps --with-source`):

```yaml
- name: VOLTAGE_OFFSET_MV
  value: "-100"   # step 1 of docs/llm-hosting/undervolt-test-plan-2026-09.md
```

Confirm it landed:

```sh
kubectl -n kube-system logs ds/amdgpu-undervolt | grep VDDGFX
# expect: set card0 VDDGFX offset to -100mV   (NOT "... failed: wanted")
kubectl -n ai exec deploy/qwen38-27b-vllm -- amd-smi metric -m --power | grep -E 'GFX_VOLTAGE|SOCKET'
```

## Steps

Sequence: **-82 (baseline) → -100 → -120 → -140**. Each step is a separate PR
and a separate soak. Do not skip a step, do not go past -140 in this plan.

### Step 0 — healthy baseline with GPU columns (no voltage change)

Required before step 1. We have latched-state clock data only.

1. Leave the logger running (`~/.local/state/vllm-itl-log/log.sh`, 60s
   cadence, CSV already has the GPU columns).
2. At the next pod restart (planned or the alert-driven one), capture the
   first 2h of uptime at -82mV. Take a manual snapshot at ~30min uptime while
   ITL < 55ms, same command as tonight, save as
   `gpu-snapshots/healthy_<ts>.txt`.
3. Record: GFX clock range while ITL < 55ms, hotspot, and the uptime at which
   ITL first crosses 55ms for ≥5 consecutive rows (tonight: ~19:47Z onset,
   obviously visible by ~50min into the episode).

**Gate 0:** the hypothesis is only worth testing if, at -82mV, GFX clock
during ITL<55ms is measurably above the latched range (i.e. the median of
healthy rows is > ~3200MHz and the median of latched rows is < ~3000MHz).
If healthy and latched rows show the same clock distribution, the latch is
not clock-driven and **this plan stops here** — deeper undervolt would be
solving a problem the card doesn't have. Write that result in the table and
close the plan.

### Step 1 — -100mV

1. PR the env change. Merge, confirm as above.
2. Immediately (T+0..T+5min): `dmesg` on the host via
   `talosctl -n 192.168.0.11 dmesg | grep -iE 'amdgpu|gpu reset|ring .* timeout|page fault'`
   — must show nothing new since the pod recreation timestamp.
3. Soak **minimum 3h under real traffic, spanning one full latch onset
   window**. Tonight's latch took ~50min to become obvious, and the point of
   the test is what happens *after* heat-soak, so a 30-minute "seems fine" is
   worthless. Preferably one soak that includes a vLLM restart so the
   healthy→latched transition is observed at the new offset.
4. Snapshot at the same uptime/ITL state as the step-0 baseline and as the
   latched baseline, save as `gpu-snapshots/<state>_m100_<ts>.txt`.

**Stable means all of:** no amdgpu reset/ring timeout/page fault in dmesg for
the whole soak; vLLM pod restart count unchanged
(`kubectl -n ai get pod -l app.kubernetes.io/name=qwen38-27b-vllm`); no
`GFX_VOLTAGE` reading that fails to drop vs the 814mV latched baseline at
comparable clock (if the offset didn't lower the voltage, it isn't doing
anything); no new NaN/garbage outputs reported by users or the gateway logs.

**Unstable means any of:** `amdgpu: GPU reset`, `ring gfx timeout`,
`*ERROR* ... page fault`, a Xid-equivalent hang, NPD kernel-event condition on
the node (`kubectl describe node control-1`), vLLM pod crash/restart, a
`VDDGFX offset failed` log line, or garbage model output. Any one → roll back
(below), record it, and the plan ends at the previous step.

**Improvement means** (compare same uptime band and same ITL state against
the -82 baselines):

- median GFX clock in the latched band up by ≥100MHz at the same 249-250W, or
- the uptime at which ITL first crosses 55ms for ≥5 consecutive rows moves
  later by ≥30min, or
- ITL no longer latches within the soak window at all.

Anything smaller than that is inside the 2497-3276MHz sample noise and counts
as "no effect" — do not proceed to the next step on a no-effect result unless
the goal is explicitly to find the stability ceiling, in which case say so in
the results table.

### Step 2 — -120mV

Same procedure and gates as step 1. Only proceed if step 1 was **stable**
(improvement is not required to continue; instability is required to stop).

### Step 3 — -140mV

Same procedure and gates. This is the last step in this plan. Going past
-140 (toward the -170 anecdote) needs a new plan with a clean-output eval
(the stability failure mode at deep undervolt on this silicon can be silent
compute errors, not a crash — a token-level A/B against -82 outputs on a fixed
prompt set is the minimum before trusting anything below -140).

## Rollback

Seconds-fast, no vLLM impact:

```sh
kubectl -n kube-system set env ds/amdgpu-undervolt VOLTAGE_OFFSET_MV=-82
kubectl -n kube-system logs ds/amdgpu-undervolt | grep VDDGFX   # expect -82mV
```

Then make git agree so Flux doesn't re-apply the test value on the next
reconcile: `git revert <step commit>` on a branch, PR, merge. If git already
says -82 (you were testing with `set env` only), Flux converges by itself
within 1h and the `set env` above just makes it immediate.

If the GPU is wedged hard enough that the offset write itself fails
(`VDDGFX offset failed` or the DaemonSet pod stuck), the offset is still
volatile: a node reboot returns the card to stock and the DaemonSet re-applies
-82 from git on boot. That is the actual worst case and it costs one
control-1 reboot (jellyfin + vLLM outage, ~minutes, plus the hours-long vLLM
cold-cache admission stall documented in memory
`project_vllm_restart_admission_stall`).

## Risks

- **Live production node.** control-1 hosts the only GPU, the vLLM pod
  serving real traffic (Hermes, gateway consumers), and Jellyfin transcodes.
  An unstable offset does not fail politely; it fails as a GPU reset that
  kills every process holding the device. Plan each step for a window where a
  reset is tolerable, and tell whoever is on the other end.
- **Silent instability.** Deep undervolt on GPUs can produce wrong math
  before it produces a crash. Watch model output quality during soaks, not
  just dmesg. Steps 1-2 are unlikely to hit this; step 3 might.
- **Confounds with the running latch investigation.** The ITL logger is the
  measurement instrument for *both* the latch diagnosis and this test. Do not
  change the offset mid-episode without writing the exact timestamp into the
  results table below; otherwise the CSV becomes uninterpretable for both
  questions. Prefer changing the offset right after a vLLM restart (start of
  a clean uptime curve).
- **Heat-soak state carries across the change.** Because the vLLM pod stays
  up, a voltage change applied 2h into a latched episode tests "does deeper
  offset *recover* clock on a hot die", which is a different (and also
  useful) question from "does it *delay* the decay from cold". Record which
  one each measurement is.
- **Cooling headroom is not the lever.** Hotspot 92-95°C is 15°C under the
  110°C slowdown point, and the fan sweep showed the curve buys noise, not
  temperature. Don't be tempted to bundle a fan change in "while we're here".

## Non-goals / do not

- Do not jump to -170mV. Single anecdote, different board, no rigor.
- Do not change `POWER_LIMIT_WATTS` (250W chosen on a completed sweep: +15%
  PP for -2.8% tok/W vs the 210W efficiency winner) or `FAN_TARGET_TEMP_C` /
  `ACOUSTIC_TARGET_RPM` / `ACOUSTIC_LIMIT_RPM` (also swept, 76°C/4000rpm with a
  4500rpm cap, deliberate). This plan varies one knob.
- Do not present any tok/s delta from this test as a throughput result.
  Decode is flat vs power on this card; if decode "improves", it is because
  the latch is gone, not because the card got faster.
- Do not run a step overlapping a benchmark, a vLLM config change, or an
  image bump. One variable.
- Do not delete/patch the vLLM InferenceService as part of this; the
  DaemonSet is the only object touched.

## Results (fill in)

| step | mV | applied at (UTC) | vLLM uptime at apply | soak (h) | dmesg clean | healthy clock median (MHz) | latched clock median (MHz) | ITL>55ms onset (uptime) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 0 | -82 | n/a | | | | | 2959 (8 rows, 20:52-20:59Z) | ~19:47Z, uptime ~4000s | baseline |
| 1 | -100 | 2026-10-27 | | | | | | | stable: quality gate bit-identical to -82, live since `c8eb817c3` |
| 2 | -120 | 2026-10-27 | | | yes | | | | unstable: 11/60 greedy prompts diverged |
| 3 | -140 | | | | | | | | |
