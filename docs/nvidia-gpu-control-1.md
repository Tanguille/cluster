# NVIDIA RTX 2070 on control-1

The RTX 2070 (TU106) is passed through to the control-1 VM next to the R9700. Stack: signed
NVIDIA open modules built into the custom kernel pipeline, the toolkit extension, and the GPU
Operator (driver and toolkit disabled) with time-slicing, `nvidia.com/gpu` x8. Plan and spike
record: `docs/plans/nvidia-gpu-operator-control-1.md` in git at `764b538101`. Workloads on it:
[llama.cpp services](#llamacpp-services-on-the-2070), [Fenrir](../kubernetes/apps/games/fenrir/)
game streaming and the solar-gated [Pearl miner](pearl-solar-mining.md).

Every step below is owner-run. **[ASK]** marks steps that touch a live system or delete;
nothing here runs from an agent session without an explicit OK. Node IPs: control-1
`192.168.0.11`, control-2 `192.168.0.12`, control-3 `192.168.0.13`. The commands assume the
pinned `talosctl` ([talos/README.md](../talos/README.md) "Common tasks").

## Before a driver, kernel or Talos change

Do not start unless all hold. Control-1 reboots and hosts osd.3.

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
- Baseline for the checks after the change:

  ```sh
  talosctl -n 192.168.0.11 ls /sys/class/drm > /tmp/drm-sys-before.txt
  talosctl -n 192.168.0.11 ls /dev/dri       > /tmp/drm-dev-before.txt
  kubectl -n media exec deploy/jellyfin -- ls -l /dev/dri > /tmp/drm-jellyfin-before.txt
  ```

- Nothing else is claimed on the nodes (`~/.claude/sessions.md`) and no tuppr roll is in flight.

## After the change

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

3. GPU metrics reach VictoriaMetrics: query `nvidia_smi_temperature_gpu` in Grafana Explore, and the
   `nvidia-gpu-exporter` dashboard (folder `hardware`) shows the card.
4. R9700 workloads and device paths unchanged. Compare with the baseline saved before the change:

   ```sh
   diff /tmp/drm-sys-before.txt <(talosctl -n 192.168.0.11 ls /sys/class/drm)
   diff /tmp/drm-dev-before.txt <(talosctl -n 192.168.0.11 ls /dev/dri)
   diff /tmp/drm-jellyfin-before.txt <(kubectl -n media exec deploy/jellyfin -- ls -l /dev/dri)
   ```

   The only new nodes must be the 2070's `card*` and `renderD129` (from `nvidia_drm`), and the
   jellyfin VAAPI device must keep its `renderD128` path.
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
Outputs matched the former iGPU services: embeddings cosine >= 0.9997, rerank scores within 0.03 with
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

The embedder switched in place: `embeddinggemma-2` is the CUDA service with the same name,
Service and GGUF, so the stored vectors and every consumer stayed valid.

Rollback: revert #6934 plus the 2026-11-20 removal of the iGPU device plugin and GTT alerts; the
Vulkan manifests return to the iGPUs (images and GGUFs are still cached).

When control-1 is down all three are down: memini recall falls back to keyword-only and backfills
vectorless writes later, qwen-3.8-auto to plain qwen-3.8 (also on control-1).

## Pearl solar miner on the 2070

`ai/pearl-miner` is an optional workload: a Deployment at 0 replicas that KEDA raises to 1 on
solar surplus ([gate and thresholds](pearl-solar-mining.md)). It needs about 1.6 GiB of VRAM beside
the three services, starts only with 2560 MiB free, stops below 1024 MiB, and has no toleration
(control-1 has no taint). The UPS shed query does not see the 2070's draw, so the gate reads the UPS
itself. Metrics come from `nvidia_smi_*`; DCGM is disabled.
