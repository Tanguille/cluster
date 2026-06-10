# Plan: Serve Qwen3.6-27B on vLLM-ROCm (AMD R9700 / gfx1201)

Replace the broken NVIDIA `llama-server` with a **continuous-batching vLLM-ROCm** deployment
serving **Qwen3.6-27B (dense)** at 4-bit on the new **AMD Radeon AI PRO R9700** (RDNA4 / gfx1201,
32 GB), with automatic prefix caching for the agentic/shared-prefix workload.

**Branch:** `feat/vllm-qwen3.6-rocm`
**Worktree:** `.claude/worktrees/vllm-qwen3.6-rocm` (copy `.mcp.json .env CLAUDE.local.md .vscode/ .claude/`)
**Namespace:** `ai`  •  **Chart:** bjw-s `app-template` (OCIRepository)  •  **Node:** control-1 (`amd.com/gpu`)

---

## Decision record (why this shape)

Researched June 2026; full rationale lives in the session, condensed here:

| Decision | Choice | Why |
|---|---|---|
| Engine | **vLLM-ROCm + APC** | No prebuilt sglang gfx1201 image exists (sglang needs a 36-patch source build + custom Talos kernel). vLLM has a tested R9700 image, and V1 **APC is on by default** — captures ~80% of sglang RadixAttention's prefix-reuse benefit for ~0 maintenance. |
| Model | **Qwen3.6-27B dense** | Flagship dense (hidden 5120, 64 layers, gated-DeltaNet hybrid attention). User's "27b". Hybrid attn ⇒ only 16/64 layers carry KV ⇒ cheap long context. |
| Weights | **GPTQ 4-bit** — `btbtyler09/Qwen3.6-27B-GPTQ-4bit` (~22 GB) | No official Qwen 4-bit checkpoint is public (FP8 30.9 GB & BF16 54 GB don't fit 32 GB). Community GPTQ chosen (Step 2). `--quantization gptq` (NOT marlin — that's CUDA). ~22 GB weights leave ~10 GB headroom. |
| KV cache | **default** (BF16) to start; `fp8_e4m3` only if VRAM-pressured | Recipe doesn't mandate a KV dtype. Hybrid attn ⇒ tiny KV. Default fits ~128K; full 262K needs FP8 KV (Step 5 lever). Avoids the gfx1201 FP8-KV fallback risk for now. |
| Parallelism | **Single GPU, TP=1** | TP=2 on dual R9700 has an unresolved RCCL deadlock (vllm#40980). TP=1 is the reliable path. |
| Attention backend | **Triton**, `VLLM_ROCM_USE_AITER=0` | AITER C++/ASM kernels are CDNA-only; gfx1201 runs the Triton JIT path. No `HSA_OVERRIDE_GFX_VERSION` (gfx1201 is natively recognised on ROCm 7.x). |

**Image (Step 2, upstream chosen):** `docker.io/rocm/vllm@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474`
(tag `rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1`) — **official AMD**,
built native for `gfx1200;gfx1201`, ROCm 7.13 / vLLM 0.19.1 / PyTorch 2.10, built 2026-05-18
(postdates Qwen3.6). vLLM 0.19.1 ≥ recipe floor (0.17.0); `qwen3_5` gated-DeltaNet supported,
and its **linear-attention kernels are Triton-based → run on ROCm** (empirically confirmed on a
gfx1201 R9700). Two image quirks handled in the manifest: (1) its default CMD does a **runtime
`pip install` of vLLM** — we override `command: [vllm, serve]` to use the baked build (validate in
Step 4; fall back to pinned-install or a thin downstream image if the baked vLLM is non-functional);
(2) no serve entrypoint.

**Honest caveats:** gfx1201 is community-validated, not in AMD's *documented* support list (docs name
MI300/MI325/MI355). Known risks to watch in Step 4: container-startup bug (vllm#40081 — amdsmi /
`device_count()==0` inside containers), and whether the ROCm GPTQ kernel path is performant on gfx1201.

---

## Process Instructions

- After completing each step, update this plan with the current status.
- Pause for user confirmation before proceeding to the next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of this plan have been
  consolidated into existing documentation, this plan file can be removed. If there is no relevant
  existing documentation, this plan should be reworked into a reference document.

**Important:** Every prompt should verify the branch and worktree before doing any work
(`git branch --show-current` ⇒ `feat/vllm-qwen3.6-rocm`; `pwd` ⇒ the worktree).

---

## Step 0 — Branch & worktree  ·  Status: ✅ done

> Worktree `.claude/worktrees/vllm-qwen3.6-rocm` on branch `feat/vllm-qwen3.6-rocm`.
> Copied `.mcp.json`, `.vscode/`, `.claude/` (excl. worktrees). `.env`/`CLAUDE.local.md` absent in repo (skipped).

1. `git pull && git status` on `main`.
2. Create branch + worktree:
   ```
   git worktree add .claude/worktrees/vllm-qwen3.6-rocm -b feat/vllm-qwen3.6-rocm
   ```
3. Copy `.mcp.json .env CLAUDE.local.md .vscode/ .claude/` into the worktree.
4. `cd` into the worktree; confirm branch.

**Continue prompt:** "Verify branch/worktree, then do Step 1: expose /dev/kfd."

---

## Step 1 — Expose `/dev/kfd` via the generic-device-plugin  ·  Status: ✅ manifest edited (pending commit + Flux reconcile)

ROCm compute needs **both** `/dev/kfd` (the compute node) and `/dev/dri/renderD*`. The plugin
(`kubernetes/apps/kube-system/generic-device-plugin/app/helmrelease.yaml`) currently exposes only
`/dev/dri`. Add `/dev/kfd` to the existing `dri` group so a single `squat.ai/dri` claim grants both:

```yaml
- --device
- |
  name: dri
  groups:
    - count: 4
      paths:
        - path: /dev/dri
        - path: /dev/kfd   # ROCm compute node — required by vLLM
```

> `/dev/kfd` is a single shared node; binding it into the same group is safe for concurrent
> consumers. Jellyfin/fileflows (VAAPI) ignore it; vLLM uses it. Keep `count: 4` (headroom).

Commit. Let Flux reconcile; confirm the daemonset still advertises `squat.ai/dri`.

**Continue prompt:** "Verify branch/worktree, then do Step 2: confirm the 4-bit format + image."

---

## Step 2 — Confirm image + working 4-bit format on gfx1201  ·  Status: ✅ done

> **Resolved:** upstream `rocm/vllm` gfx120X image (digest above) instead of the community build.
> Model `btbtyler09/Qwen3.6-27B-GPTQ-4bit`, `--quantization gptq`. No official Qwen 4-bit exists
> publicly (FP8/BF16 too big for 32 GB). KV left default to start. vLLM 0.19.1 confirmed to support
> the model; GDN kernels are Triton (ROCm-capable). Remaining empirical unknowns deferred to Step 4:
> baked-vLLM-runs-without-bootstrap, container-startup bug, GPTQ kernel perf.

Empirically settle the one real unknown before writing manifests:

1. **Image:** resolve `tcclaviger/vllm-rocm-rdna4-mxfp4` to a **digest** (`docker buildx imagetools
   inspect` or `crane digest`). Record vLLM + ROCm versions. Decide: consume directly (pin digest)
   vs mirror to GHCR for supply-chain safety.
2. **4-bit format:** determine which quant has working gfx1201 kernels in this image. Candidates,
   in preference order:
   - **MXFP4** — the image is built for it (fused dequant kernels); may be the smoothest path.
   - **AWQ** — `mattbucci/Qwen3.6-27B-AWQ` is published & validated on R9700 (under sglang).
   - **GPTQ** — fallback.
   Quick smoke test (single GPU, short ctx) for each available checkpoint; keep the one that loads
   and generates correctly. **This choice sets `--quantization` and the model repo in Step 3.**
3. Pick the model repo accordingly (e.g. `mattbucci/Qwen3.6-27B-AWQ`, or an MXFP4 Qwen3.6-27B).

**Continue prompt:** "Verify branch/worktree, then do Step 3: write the vLLM app manifests."

---

## Step 3 — Create the `vllm` app  ·  Status: ✅ done

> Created `kubernetes/apps/ai/vllm/{ks.yaml, app/{helmrelease,pvc,kustomization}.yaml}` and added
> `./vllm/ks.yaml` to the namespace kustomization. `kustomize build --enable-helm` passes (exit 0).
> Manifest reflects the resolved Step-2 decisions (upstream image digest, GPTQ model, `command:
> [vllm, serve]` override, `squat.ai/dri` + render/video groups + seccomp Unconfined, /dev/shm
> emptyDir, default KV, `--max-model-len 65536` to start).

New app dir `kubernetes/apps/ai/vllm/` mirroring the `llama-server` layout.

**`ks.yaml`** — copy `llama-server/ks.yaml`, rename to `vllm`, path `./kubernetes/apps/ai/vllm/app`.

**`app/pvc.yaml`** — `vllm` PVC, `openebs-hostpath`, `50Gi` (27B 4-bit ≈ 15 GB + HF cache).

**`app/kustomization.yaml`** — list `pvc.yaml`, `helmrelease.yaml` (+ servicemonitor later).

**`app/helmrelease.yaml`** — key differences from llama-server (the broken NVIDIA bits → AMD):

```yaml
values:
  defaultPodOptions:
    # NOTE: drop runtimeClassName: nvidia entirely
    affinity:                         # control-1 AMD node (see jellyfin)
      nodeAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
          nodeSelectorTerms:
            - matchExpressions:
                - { key: amd.com/gpu, operator: In, values: ["true"] }
    securityContext:
      runAsUser: 0
      supplementalGroups: [44, 226]   # video, render — open /dev/kfd & /dev/dri
      seccompProfile: { type: Unconfined }   # ROCm needs it
  controllers:
    app:
      containers:
        app:
          image:
            repository: <tcclaviger/vllm-rocm-rdna4-mxfp4 or mirror>
            tag: <pinned-digest>
          env:
            TZ: ${TIMEZONE}
            HF_HOME: /cache
            VLLM_ROCM_USE_AITER: "0"        # CDNA-only kernels off
            # do NOT set HSA_OVERRIDE_GFX_VERSION — gfx1201 is native on ROCm 7.x
          args:
            - --model
            - <Qwen3.6-27B-{AWQ|MXFP4} repo>
            - --quantization
            - <awq|awq_marlin|mxfp4 — from Step 2>
            - --kv-cache-dtype
            - fp8_e4m3                       # fall back to "auto" if it degrades
            - --max-model-len
            - "65536"                        # start here; raise toward 262144 in Step 5
            - --enable-prefix-caching        # APC (default-on in V1; explicit for clarity)
            - --attention-backend
            - triton
            - --gpu-memory-utilization
            - "0.92"
            - --served-model-name
            - qwen3.6-27b
            - --host
            - 0.0.0.0
            - --port
            - "8000"
          probes:                            # model load is slow — generous startup
            startup: { failureThreshold: 60, periodSeconds: 10 }  # ~10 min
            liveness/readiness: GET /health on 8000
          resources:
            requests: { cpu: 2, memory: 8Gi, squat.ai/dri: 1 }
            limits:   { cpu: 12, memory: 32Gi, squat.ai/dri: 1 }
  persistence:
    cache: { existingClaim: vllm, globalMounts: [{ path: /cache }] }
    shm:   { type: emptyDir, medium: Memory, sizeLimit: 8Gi, globalMounts: [{ path: /dev/shm }] }
  service: { app: { ports: { http: { port: 80, targetPort: 8000 } } } }
  route:   # same envoy-internal + homepage annotations as llama-server
```

Add `vllm` to `kubernetes/apps/ai/kustomization.yaml`. Run `kustomize build`/lint locally.

**Continue prompt:** "Verify branch/worktree, then do Step 4: deploy & bring up the GPU."

---

## Step 4 — Deploy & verify GPU bring-up  ·  Status: ☐ not started

1. Commit; let Flux reconcile (`flux reconcile ks vllm`).
2. Pod schedules on control-1, claims `squat.ai/dri`.
3. `kubectl exec` → `rocm-smi` shows the R9700; `/dev/kfd` and `/dev/dri/renderD*` present.
4. Logs: vLLM detects gfx1201, loads the 4-bit checkpoint, **no FP32-fallback warning** on the quant path.
5. If startup fails on amdsmi (vllm#40081) → apply the documented workaround (privileged device
   access / monkey-patch) and record it here.
6. Smoke test: `curl .../v1/chat/completions` returns a coherent completion.

**Continue prompt:** "Verify branch/worktree, then do Step 5: tune context, KV, and batching."

---

## Step 5 — Tune context / KV / batching, then benchmark  ·  Status: ☐ not started

1. Raise `--max-model-len` toward **262144**; watch VRAM headroom (`rocm-smi`).
2. Confirm `fp8_e4m3` KV actually accelerates; if it silently degrades, set `--kv-cache-dtype auto`
   and accept slightly less context.
3. Batching test: fire N concurrent requests; confirm continuous batching (throughput scales),
   and APC prefix-cache hit-rate is reported for shared-prefix traffic.
4. Record: max stable context, tok/s single vs batched, max concurrent sequences, VRAM at idle/load.
5. Set `--max-num-seqs` / final `--gpu-memory-utilization` from observed headroom.

**Continue prompt:** "Verify branch/worktree, then do Step 6: wire into LiteLLM/open-webui & retire llama-server."

---

## Step 6 — Integrate & decommission  ·  Status: ☐ not started

1. Point **LiteLLM** (`kubernetes/apps/ai/litellm/`) at `http://vllm.ai.svc.cluster.local` as the
   `qwen3.6-27b` backend; verify open-webui sees it.
2. **Retire `llama-server`:** it still requests `nvidia.com/gpu` / `runtimeClassName: nvidia` and
   cannot schedule on the AMD node. **Caveat:** it also served the `qwen3-embedding` GGUF — do NOT
   delete blindly. Either (a) move embeddings to TEI-on-GPU (see `project_vmcp_tei_batchsize_restart`),
   or (b) keep a CPU/embedding-only path first. Remove `llama-server` from the namespace kustomization
   only once embeddings are re-homed.
3. Update ServiceMonitor/Grafana dashboard for vLLM metrics (`/metrics`).

**Continue prompt:** "Verify branch/worktree, then do the final documentation pass."

---

## Step 7 — Final docs pass & PR  ·  Status: ☐ not started

1. Fold the durable bits (image+digest, working quant format, gfx1201 env vars, kfd exposure,
   measured limits) into repo docs / app README.
2. Remove this plan file (or rework into a reference doc).
3. Open PR with `gh` (no Claude attribution, per project convention). Do **not** push until asked.
