# NVIDIA RTX 2070 on control-1: rollout and rollback

The RTX 2070 (TU106) is passed through to the control-1 VM next to the R9700. Stack: signed
NVIDIA open modules built into the custom kernel pipeline, the toolkit extension, and the GPU
Operator (driver and toolkit disabled) with time-slicing, `nvidia.com/gpu` x8. Plan and spike
record: [the plan](plans/nvidia-gpu-operator-control-1.md). Workloads on it:
[llama.cpp services](#llamacpp-services-on-the-2070).

Every step below is owner-run. **[ASK]** marks steps that touch a live system, push, or delete;
nothing here runs from an agent session without an explicit OK. Node IPs: control-1
`192.168.0.11`, control-2 `192.168.0.12`, control-3 `192.168.0.13`. The commands assume the
pinned `talosctl` ([talos/README.md](../talos/README.md) "Common tasks").

Why this is not a normal tuppr roll: control-1's installer is rebuilt and re-pushed under the
**existing** tag (`v1.14.6-k7.3.4`), because tuppr compares the tag as a plain string and would
revert any other tag. Same tag, new digest, so the upgrade is pinned by digest. The old image is
gone from the tag, so the rollback is Talos A/B. Background:
[docker/talos-kernel/README.md](../docker/talos-kernel/README.md) "Re-pushing one node's installer".

## 0. Preconditions

Do not start unless all hold. Control-1 reboots once or twice and hosts osd.3.

- Ceph `HEALTH_OK`, no remapped or degraded PGs, no OSD down or flapping recently on control-2/3.
  The pools are size 2 / min_size 1, so while control-1 is down osd.3's PGs run on one copy: a
  failure of that peer in the window loses data. Also finish any reboot test on the TrueNAS host
  `192.168.0.27` first.

  ```sh
  kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
  kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd tree
  kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph pg stat
  ```

- Console access to the VM through the TrueNAS UI on `.27`: a passthrough boot with a new GPU
  function can stall, and a powercycle may be needed from the host side.
- Baseline for the post-checks, saved before the upgrade:

  ```sh
  talosctl -n 192.168.0.11 ls /sys/class/drm > /tmp/drm-sys-before.txt
  talosctl -n 192.168.0.11 ls /dev/dri       > /tmp/drm-dev-before.txt
  kubectl -n media exec deploy/jellyfin -- ls -l /dev/dri > /tmp/drm-jellyfin-before.txt
  ```

- Nothing else is claimed on the nodes (`~/.claude/sessions.md`) and no tuppr roll is in flight.

## 1. Build the control-1 installer [ASK: push + CI dispatch]

Needs the branch on the remote (a push, ask first). The build takes about 1-2 h and its cache is
`mode=min`, so a retry repeats the full compile. The first run is the first real test of the
`buildx` push and `make -C extensions nvidia-open-gpu-kernel-modules-production`; neither could be
run locally.

```sh
branch=feat/nvidia-gpu-operator
gh workflow run talos-kernel.yaml --ref "${branch}" -f force_node=control-1
gh run watch --exit-status "$(gh run list --workflow talos-kernel.yaml --branch "${branch}" \
    --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')"
```

`force_node` rebuilds and overwrites only `installer/control-1`; it refuses a node on the shared
schematic. Then:

1. Copy the pushed ref and digest from the job summary
   (`ghcr.io/tanguille/installer/control-1:v1.14.6-k7.3.4@sha256:<new>`). Record it in the PR.
2. Confirm a node can pull it, with the token check (bare `crane` can report success on a private
   ref through cached credentials). Expect `200` for the tag and for the digest:

   ```sh
   repo=tanguille/installer/control-1
   TOK=$(curl -s "https://ghcr.io/token?scope=repository%3A${repo}%3Apull&service=ghcr.io" | jq -r .token)
   for ref in v1.14.6-k7.3.4 sha256:<new>; do
     curl -s -o /dev/null -w "${ref} %{http_code}\n" -H "Authorization: Bearer $TOK" \
       -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
       "https://ghcr.io/v2/${repo}/manifests/${ref}"
   done
   ```

3. Confirm the tag moved and the shared image did not. The new digest must differ from the old
   control-1 one and `installer/shared` must be unchanged:

   | ref | digest before the build (2026-11-14) |
   |-----|--------------------------------------|
   | `installer/control-1:v1.14.6-k7.3.4` | `sha256:0548cb8a4c7f6887f14db7b7f66b7e67a50433974cc1776c366aae67bf4b9bd7` |
   | `installer/shared:v1.14.6-k7.3.4` | `sha256:a5649e02f452bfdc844f9927500f841f89c8fea78d8279619240bd3f0bd92ad5` |

   ```sh
   crane digest ghcr.io/tanguille/installer/shared:v1.14.6-k7.3.4   # must equal the shared row
   ```

If the build fails, stop: nothing live has changed. Read the failing step before retrying.

## 2. Diff all nodes [ASK: contacts the live nodes, read-only]

Dry runs against the running configs. control-2 and control-3 must print `No changes.`;
control-1 must show only the NVIDIA additions (`KernelModuleConfig` x3, `SysctlConfig`, the
`nvidia.com/gpu` label).

```sh
just talos diff-node control-1 192.168.0.11
just talos diff-node control-2 192.168.0.12
just talos diff-node control-3 192.168.0.13
```

Anything else on control-2/3, or any unrelated control-1 change, is a blocker.

## 3. Upgrade control-1 by digest [ASK: reboots a live node]

Only control-1, never control-2/3. Mirrors the `upgrade-node` recipe, but with the digest, which
that recipe cannot express (it reads the tag from the render).

```sh
talosctl -n 192.168.0.11 upgrade \
  -i ghcr.io/tanguille/installer/control-1:v1.14.6-k7.3.4@sha256:<new> \
  -m powercycle --timeout=15m
```

Verify the node pulled the new digest and runs the new build:

```sh
talosctl -n 192.168.0.11 dmesg | grep -E 'performing upgrade|pulling'    # shows the @sha256 ref
talosctl -n 192.168.0.11 get extensions | grep -i nvidia                 # modules 595.112.07-v1.14.6, toolkit
talosctl -n 192.168.0.11 read /proc/version                              # build time differs from control-2/3
kubectl get node control-1                                               # Ready
kubectl -n kube-system get pods -o wide | grep "cilium.*control-1"
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status         # osd.3 back up, PGs active+clean
```

Wait for Ceph to be `HEALTH_OK` again before the next step. The node boots with the **old**
machine config, so the nvidia modules are still blacklisted and not loaded; that is expected.
tuppr sees an identical version string and does nothing.

## 4. Apply the machine config [ASK: applies to a live node]

Do this only after step 3 succeeded: the `KernelModuleConfig` documents load modules the old
image does not have (source says a missing module only makes the controller retry, but this was
never verified on a node).

```sh
just talos diff-node control-1 192.168.0.11    # re-check, expect only the NVIDIA additions
just talos apply-node control-1 192.168.0.11
```

If Talos asks for a reboot, do it, then re-run the node checks from step 3. Then:

```sh
talosctl -n 192.168.0.11 read /proc/modules | grep -E '^nvidia'          # nvidia, nvidia_uvm, nvidia_modeset
talosctl -n 192.168.0.11 dmesg | grep -iE 'nvidia|module verification|sig'
```

A `module verification failed` or `Key was rejected by service` line means the modules were
signed by a different build than the running kernel: stop and roll back (below).

## 5. Roll out the operator [ASK: merge/push, Flux apply]

The operator has nothing to find before step 4, so merge the PR (or reconcile) only now. The
merge-triggered `talos-kernel` run no-ops because the tag exists, so the overwritten control-1
image survives it.

```sh
just reconcile                                    # after the merge
kubectl -n gpu-operator get pods -o wide          # all operands on control-1 only (nfd-worker runs everywhere by design)
kubectl get runtimeclass nvidia
```

`cdi.enabled: false` is set in the HelmRelease but not yet confirmed by the owner. If the
operator operands or the test pod below fail on a missing CDI device, flip it to `true` in
`kubernetes/apps/gpu-operator/gpu-operator/app/helmrelease.yaml` (the operator then also creates
the `nvidia-cdi` and `nvidia-legacy` RuntimeClasses; workloads keep using `nvidia`).

If the validator wants `nvidia_drm`, add a `KernelModuleConfig` with `parameters: [modeset=0]`
and nothing else (see "R6" in the plan); `modeset=1` adds a second DRM card.

## 6. Post-checks

1. Allocatable equals the time-slicing replicas (8):

   ```sh
   kubectl get node control-1 -o jsonpath='{.status.allocatable}' | jq 'with_entries(select(.key|test("gpu")))'
   ```

   Expect `nvidia.com/gpu: "8"` and `amd.com/gpu` unchanged.

2. A test pod sees the TU106. The image must be glibc (the toolkit injects `nvidia-smi`; busybox
   will not run it), for example any Debian or Ubuntu image already pinned in the repo:

   ```sh
   kubectl -n gpu-operator apply -f - <<'EOF'
   apiVersion: v1
   kind: Pod
   metadata:
     name: nvidia-smi-test
   spec:
     restartPolicy: Never
     runtimeClassName: nvidia
     nodeSelector:
       kubernetes.io/hostname: control-1
     containers:
       - name: smi
         image: <glibc-image>
         command: [nvidia-smi]
         resources:
           limits:
             nvidia.com/gpu: 1
   EOF
   kubectl -n gpu-operator logs nvidia-smi-test       # TU106 / RTX 2070, driver 595.112.07
   kubectl -n gpu-operator delete pod nvidia-smi-test   # [ASK: delete]
   ```

3. DCGM metrics reach VictoriaMetrics: query `DCGM_FI_DEV_GPU_TEMP` in Grafana Explore, and the
   `nvidia-dcgm` dashboard (folder `hardware`) shows the card.
4. R9700 workloads and device paths unchanged (R6). Compare with the baseline from step 0:

   ```sh
   diff /tmp/drm-sys-before.txt <(talosctl -n 192.168.0.11 ls /sys/class/drm)
   diff /tmp/drm-dev-before.txt <(talosctl -n 192.168.0.11 ls /dev/dri)
   diff /tmp/drm-jellyfin-before.txt <(kubectl -n media exec deploy/jellyfin -- ls -l /dev/dri)
   ```

   There must be no new `card*` or `renderD*` and the jellyfin VAAPI device must keep its
   `renderD128` path. A new DRM node means `nvidia_drm` loaded with `modeset=1`.
5. R9700 temperature recheck (the 2070 adds heat next to it). Run a
   5-minute full-power load on the R9700 (a vLLM or radiance bench burst) while reading the
   junction temperature, and **stop at junction 97 C**. For reference the 2026-11-01 runs read
   junction 85 C at 250 W ([radiance runbook](llm-hosting/radiance-2026-11.md)). amdgpu hwmon
   `temp1` is edge, `temp2` junction, `temp3` memory; confirm with `temp*_label`:

   ```sh
   talosctl -n 192.168.0.11 ls /sys/class/hwmon
   talosctl -n 192.168.0.11 read /sys/class/hwmon/hwmon<N>/temp2_label   # find the amdgpu one, expect "junction"
   talosctl -n 192.168.0.11 read /sys/class/hwmon/hwmon<N>/temp2_input   # millidegrees C
   ```

6. Ceph still `HEALTH_OK`, `kubectl get pods -A | grep -vE 'Running|Completed'` clean.

## Rollback

Control-2/3 are never touched, so every path below only concerns control-1.

- **Primary, after step 3: Talos A/B.** The previous kernel, extensions and modules are
  self-consistent under `sig_enforce`. [ASK: reboots a live node]

  ```sh
  talosctl -n 192.168.0.11 rollback
  ```

  There is no "upgrade back to the previous tag": the tag now points at the new image. The
  machine config from step 4 (the extra `KernelModuleConfig` documents) is harmless on the old
  image; revert the `talos/` change and re-apply when convenient.
- **Second path, UNVERIFIED:** the previous manifest `installer/control-1@sha256:0548cb8a4c7f6887f14db7b7f66b7e67a50433974cc1776c366aae67bf4b9bd7`
  survives on GHCR as an untagged version only until someone prunes it. If it still resolves
  (token check from step 1 with that digest), `talosctl -n 192.168.0.11 upgrade -i
  ghcr.io/tanguille/installer/control-1@sha256:0548cb8a...` is possible. Do not rely on it.
- **Operator only:** revert the commit that added `kubernetes/apps/gpu-operator/` and
  `just reconcile` [ASK: Flux apply, deletes the operator; Flux prunes it]. Or
  `flux suspend kustomization gpu-operator` to stop reconciling without deleting.
- **Before step 3:** nothing live changed. Abandon the branch; a forced build only leaves the
  overwritten control-1 tag (see the README consequences) and overwritten intermediates.

## After the rollout

The plan stays until the rollout has happened. Then fold what is still true into
[docker/talos-kernel/README.md](../docker/talos-kernel/README.md) and this file, delete the plan,
and (with the owner's OK) add the facts worth keeping to `.agents/learned-workspace.md`.

## llama.cpp services on the 2070

d3-nano, bge-reranker-v2-m3 and the embedder (`embeddinggemma-2`) run on the card through
llmkube (`accelerator: cuda`, `runtimeClassName: nvidia`, one time-slice each), image
`server-cuda13` (CUDA 13.4). Its PTX loads on Turing because toolkit 1.19.1 or newer injects the
image's forward-compat `libcuda`; an older toolkit fails with "unsupported PTX toolchain"
([llama.cpp #31503](https://github.com/ggml-org/llama.cpp/issues/31503)). We run v1.19.1 and all
three services were tested on it. The first run used `server-cuda` (CUDA 12.8, b12407) and is kept
below for comparison; rolling back means pinning that image again.

Per process in production after the merge (`nvidia-smi --query-compute-apps` in each pod) and in the
first CUDA 12.8 pods:

| Service | Flags | VRAM MiB, CUDA 13 (production) | CUDA 12.8 |
| --- | --- | --- | --- |
| embeddinggemma-2 Q8_0 | 8 slots, ubatch 8192, `-fa on` | 1344 | 1388 (weights 144, compute 1088) |
| bge-reranker-v2-m3 Q8_0 | 4 slots x 2048, `-fa on` | 500 | 510 |
| d3-nano Q8_0 | 2 x 4096, ubatch 2048 | 2386 | 2390 (weights 1908, KV 96, recurrent 39, compute 224) |
| All three loaded | | 4227 of 7785 (DCGM), 3558 MiB (3.5 GiB) spare | peak 4292, 3494 spare |

CUDA 13 costs the same VRAM as 12.8. The throwaway test pods before the merge showed about 1.1 GiB
more (2064 and 2848 MiB for the embedder and d3-nano) because they loaded the models with `-hf`, which
also pulled each repo's vision projector; production loads the plain GGUF. The concurrent-load peak
was not re-measured on CUDA 13.
CUDA 13 latency, serial, after the first request: rerank of 15 documents p50 0.12 s, p95 0.13 s
(0.39 s p95 on 12.8); 20 embeddings 0.12 s median; d3 effort decisions p50 0.048 s, p95 0.061 s.
Slots (CUDA 13, same VRAM either way): the reranker at 4 slots (context 8192) takes 0.06 s p50 for 15
documents instead of 0.12 s, and p95 0.20 s instead of 0.50 s under 4 concurrent callers; the embedder
at 8 slots (context 65536) takes 0.87 s for the 194-item batch instead of 1.13 s, and a single query
sent during that batch p95 0.72 s instead of 0.90 s. d3-nano never used its second slot, so it stays at 2.
Outputs match the live iGPU services: embeddings cosine >= 0.9997, rerank scores within 0.03 with
the same order, d3 picks the same effort with probabilities within 0.011.

Each process also holds ~110 MiB of CUDA context and ~1 GiB of host RSS (0.3-0.4 GiB anonymous,
the rest mmapped model pages). llama.cpp allocates its buffers at load, so VRAM is flat under load.
`NvidiaVramLow` fires below 512 MiB free: time-slicing gives no VRAM isolation between pods.

Turing is built only as PTX, so the driver JIT-compiles the kernels: with an empty cache the
embedder took 59 s to load and 27 s for its first request; with a warm cache 1.3 s and 0.08 s.
`CUDA_CACHE_PATH` points into a per-service subPath of the RWX model-cache PVC
(`cuda-cache/<service>`, under 0.15 GiB each), so a restart or a node move stays warm and the
server cannot write the model files. A driver bump rebuilds it once.

Against the iGPU services (same inputs): embeddings cosine >= 0.9998, bge scores within 0.11
logit, d3-nano identical on the 8-ball bench (27 wrong of 1119) and the effort prompts. Latency:
194 x 400-char embeddings 0.9 s (7.4 s on the iGPU), rerank of 15 x 1024-char docs p95 0.39 s
idle and 0.97 s beside embedder load, d3 effort decisions p50 0.05 s idle, 0.12 s under load.
Board power reached 165 W under the combined load; the UPS shed query does not see the 2070.

The embedder switched in place: `embeddinggemma-2` is now the CUDA service, with the same name,
Service and GGUF, so the stored vectors and every consumer stay as they are. Its JIT cache starts
empty, so the first start after the merge takes about 90 s (59 s load, 27 s first request); memini
recall falls back to keyword-only and the optimizer's embeds fail until it is Ready. Later restarts
are warm.

Rollback: revert the commit; the Vulkan manifests return to the iGPUs (images and GGUFs are still
cached). When control-1 is down all three are down: memini recall falls back to keyword-only and
backfills vectorless writes later, qwen-3.8-auto to plain qwen-3.8 (also on control-1).
