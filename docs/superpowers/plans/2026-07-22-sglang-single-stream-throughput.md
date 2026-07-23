# SGLang Single-Stream Decode Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and measure an RDNA4 SGLang candidate containing patches 086 and 087 against the current image on the same R9700, without any unapproved restart or production image change.

**Architecture:** A manual-only GitHub Actions workflow builds the candidate under a dedicated experiment tag and reports its authoritative digest. A valid but normally unreferenced Flux manifest defines a `Recreate` benchmark Deployment, Service, and experiment-only Triton-cache PVCs. During an explicitly approved maintenance window, a GitOps commit sets the production `InferenceService` to `replicas: 0` and enables the control benchmark; later GitOps changes recreate it with the candidate. SGLang v0.5.15's native benchmark CLI supplies deterministic fixed-length requests and JSONL metrics. A final GitOps change prunes the benchmark resources and restores production.

**Tech Stack:** Docker Buildx/GHCR, GitHub Actions, FluxCD/Kustomize, Kubernetes, SGLang v0.5.15 `sglang.benchmark.serving`, Bash, JSONL.

---

## File Structure

- Create: `.github/workflows/build-sglang-rdna4-throughput.yaml` — manual-only candidate build with a distinct experiment tag and digest-authoritative output.
- Create: `kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.yaml` — dormant benchmark Deployment, Service, and two experiment-only Triton PVCs; not referenced by Kustomize outside an approved window.
- Create: `kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh` — fixed control/candidate matrix and JSONL validation using SGLang's native CLI.
- Create: `kubernetes/apps/ai/llmkube/models/test-qwen36-decode-ab.sh` — local Bash/Python static contract test for the runner, runbook, plan, and safety invariants.
- Create: `kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.md` — exact maintenance, benchmark, result-capture, rollback, and cleanup runbook.
- Modify temporarily during the approved window: `kubernetes/apps/ai/llmkube/ks.yaml`, `kubernetes/apps/ai/llmkube/models/kustomization.yaml`, and `kubernetes/apps/ai/llmkube/models/qwen36-27b-sglang.yaml`.
- Modify after measurement: `docs/llm-hosting/sglang-benchmarks.md` — append the exact control/candidate evidence.

### Task 1: Add the manual candidate-image workflow

**Files:**
- Create: `.github/workflows/build-sglang-rdna4-throughput.yaml`

- [ ] **Step 1: Create the complete manual-only workflow**

```yaml
---
name: Build sglang-rdna4 throughput candidate
run-name: Build sglang-rdna4 throughput candidate ${{ inputs.run_token }}

on:
  workflow_dispatch:
    inputs:
      run_token:
        description: Benchmark build identifier
        required: true
        type: string

concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: true

permissions:
  contents: read
  packages: write

env:
  IMAGE: ghcr.io/tanguille/sglang-rdna4
  TAG: v0.5.15-gfx1201-decode-ab-086-087-${{ inputs.run_token }}
  FORK_REF: a7e69a2cf9e13fcbf366a18aefbfb7798da4edcb

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Validate run token
        env:
          RUN_TOKEN: ${{ inputs.run_token }}
        run: |
          set -euo pipefail
          [[ "${RUN_TOKEN}" =~ ^[a-z0-9-]{1,94}$ ]]

      - name: Move docker storage to /mnt
        run: |
          sudo systemctl stop docker.service docker.socket
          sudo mkdir -p /mnt/docker
          echo "$(sudo jq '. + {"data-root":"/mnt/docker"}' /etc/docker/daemon.json 2>/dev/null || echo '{"data-root":"/mnt/docker"}')" \
            | sudo tee /etc/docker/daemon.json
          sudo systemctl start docker.service
          docker info --format 'docker data root: {{.DockerRootDir}}'
          df -h /mnt

      - name: Set up Buildx
        uses: docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0

      - name: Log in to GHCR
        uses: docker/login-action@af1e73f918a031802d376d3c8bbc3fe56130a9b0 # v4.4.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push candidate
        id: build
        uses: docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7.3.0
        with:
          context: docker/sglang-rdna4
          push: true
          tags: ${{ env.IMAGE }}:${{ env.TAG }}
          build-args: |
            FORK_REF=${{ env.FORK_REF }}
          provenance: false
          sbom: false

      - name: Summary
        run: echo "Pushed \`${IMAGE}:${TAG}@${{ steps.build.outputs.digest }}\`" >> "${GITHUB_STEP_SUMMARY}"
```

Do not add a `push` trigger. Fork commit `a7e69a2c...` contains patch 087 and its history includes patch 086. The stable `v0.5.15-gfx1201` tag must not be rebuilt. The existing `Dockerfile:53-57` already exposes `ARG FORK_REF`; do not alter its production default.

- [ ] **Step 2: Validate the workflow and its trigger boundary**

Run:

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
git diff --check
python3 - <<'PY'
from pathlib import Path

workflow = Path(".github/workflows/build-sglang-rdna4-throughput.yaml").read_text()
assert "workflow_dispatch:" in workflow
assert "\n  push:" not in workflow
PY
```

Expected: all commands exit 0; the candidate workflow contains `workflow_dispatch` and no `push:` event.

- [ ] **Step 3: Commit only after the user requests a checkpoint commit**

```bash
git add .github/workflows/build-sglang-rdna4-throughput.yaml
git commit -m "ci(ai): add SGLang throughput candidate build"
```

### Task 2: Add the dormant GitOps benchmark workload

**Files:**
- Create: `kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.yaml`

- [ ] **Step 1: Create the complete dormant workload manifest**

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: qwen36-27b-decode-ab-control-triton
  namespace: ai
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: openebs-hostpath
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: qwen36-27b-decode-ab-candidate-triton
  namespace: ai
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: openebs-hostpath
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: qwen36-27b-decode-ab
  namespace: ai
spec:
  selector:
    app.kubernetes.io/name: qwen36-27b-decode-ab
  ports:
    - name: http
      port: 8000
      targetPort: http
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen36-27b-decode-ab
  namespace: ai
spec:
  replicas: 0
  revisionHistoryLimit: 0
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: qwen36-27b-decode-ab
  template:
    metadata:
      labels:
        app.kubernetes.io/name: qwen36-27b-decode-ab
    spec:
      nodeSelector:
        kubernetes.io/hostname: control-1
      securityContext:
        runAsUser: 0
        runAsGroup: 0
        supplementalGroups: [44, 226]
        seccompProfile:
          type: Unconfined
      containers:
        - name: sglang
          image: ghcr.io/tanguille/sglang-rdna4:v0.5.15-gfx1201@sha256:3d1561f6a87ce2d61f7c508f6c2f76fd6bbffb094c99d23cb097a89353355bfe
          command: [python3, -m, sglang.launch_server]
          args:
            - --model-path
            - /models/1bdc22cc0419b237/model.safetensors
            - --host
            - 0.0.0.0
            - --port
            - "8000"
            - --enable-metrics
            - --served-model-name
            - qwen-3.6
            - --tp-size
            - "1"
            - --context-length
            - "180000"
            - --mem-fraction-static
            - "0.875"
            - --chunked-prefill-size
            - "8192"
            - --max-running-requests
            - "32"
            - --quantization
            - awq
            - --kv-cache-dtype
            - fp8_e4m3
            - --reasoning-parser
            - qwen3
            - --dtype
            - bfloat16
            - --num-continuous-decode-steps
            - "16"
            - --watchdog-timeout
            - "600"
            - --attention-backend
            - triton
            - --pre-warm-nccl
            - --trust-remote-code
            - --chat-template
            - /opt/rdna4-inference/scripts/qwen3.6_devrole_chat_template.jinja
            - --tool-call-parser
            - qwen3_coder
            - --disable-custom-all-reduce
            - --disable-overlap-schedule
            - --cuda-graph-backend-decode=disabled
            - --cuda-graph-backend-prefill=disabled
            - --max-mamba-cache-size
            - "60"
            - --mamba-ssm-dtype
            - bfloat16
            - --max-queued-requests
            - "32"
            - --weight-loader-drop-cache-after-load
            - --enable-hierarchical-cache
            - --hicache-io-backend
            - direct
            - --hicache-ratio
            - "1.5"
            - --model-loader-extra-config
            - '{"num_threads":2}'
            - --schedule-conservativeness
            - "0.1"
            - --enable-mixed-chunk
            - --hicache-write-policy
            - write_through_selective
          env:
            - {name: HIP_VISIBLE_DEVICES, value: "0"}
            - {name: ROCR_VISIBLE_DEVICES, value: "0"}
            - {name: GPU_DEVICE_ORDINAL, value: "0"}
            - {name: CUDA_VISIBLE_DEVICES, value: "0"}
            - {name: HF_HUB_OFFLINE, value: "1"}
            - {name: TRANSFORMERS_OFFLINE, value: "1"}
            - {name: TRITON_CACHE_DIR, value: /cache/sglang/triton}
          ports:
            - name: http
              containerPort: 8000
          resources:
            requests:
              cpu: "2"
              memory: 24Gi
              squat.ai/dri: "1"
            limits:
              memory: 24Gi
              squat.ai/dri: "1"
          startupProbe:
            httpGet: {path: /health, port: http}
            initialDelaySeconds: 60
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 120
          livenessProbe:
            exec:
              command: ["true"]
            periodSeconds: 3600
          readinessProbe:
            tcpSocket: {port: http}
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 6
          volumeMounts:
            - {name: model-cache, mountPath: /models, readOnly: true}
            - {name: triton-cache, mountPath: /cache}
            - {name: dshm, mountPath: /dev/shm}
      volumes:
        - name: model-cache
          persistentVolumeClaim:
            claimName: qwen36-27b-model-cache
            readOnly: true
        - name: triton-cache
          persistentVolumeClaim:
            claimName: qwen36-27b-decode-ab-control-triton
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 8Gi
```

Do not add MTP/EAGLE/speculative flags. Shallow vLLM MTP measurements used a different checkpoint at approximately 420 input tokens; SGLang verification falls to 0.2 tok/s near 188K, so speculation would invalidate and endanger this deep-context kernel A/B. Never mount or clear `qwen36-27b-triton-cache`; it belongs to production.

- [ ] **Step 2: Validate without activating the workload**

Run:

```bash
mise exec -- kubectl apply --dry-run=client -f kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.yaml
mise exec -- kustomize build kubernetes/apps/ai/llmkube/models >/tmp/opencode/llmkube-models.yaml
bash .agents/skills/pr-review/scripts/validate-pr.sh
git diff --check
python3 - <<'PY'
from pathlib import Path

rendered = Path("/tmp/opencode/llmkube-models.yaml").read_text()
assert "name: qwen36-27b-decode-ab" not in rendered
PY
```

Expected: all commands exit 0. The Kustomize output must not contain `qwen36-27b-decode-ab`, proving the dormant manifest is not live.

- [ ] **Step 3: Commit only after the user requests a checkpoint commit**

```bash
git add kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.yaml
git commit -m "test(ai): add dormant SGLang decode benchmark"
```

### Task 3: Add the maintenance and benchmark runbook

**Files:**
- Create: `kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh`
- Create: `kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.md`

- [ ] **Step 1: Document read-only preflight checks**

Require a fresh explicit approval before any push, Flux reconciliation, resource activation, restart, or deletion. The runbook must run its complete read-only preflight in a strict `set -euo pipefail` subshell. It captures production Pod and InferenceService JSON and uses literal `jq -e` assertions for the committed control image, exact generated ordered args, readable cached model, Pod Ready=True, InferenceService Ready/Available, and no Failed/Degraded=True. Only a zero exit permits continuing; any drift aborts before downtime.

The exact runnable subshell block is maintained in
`kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.md`.

Abort if any assertion fails; only a zero exit permits continuing. No stop proceeds until drift, unhealthy state, or unsafe GPU work is corrected and re-reviewed.

- [ ] **Step 2: Document candidate build and immutable digest capture**

After the workflow is merged and separate push permission is granted, generate a unique UTC `run_token` (lowercase letters/digits/dashes, at most 94 characters) and dispatch the workflow with its required `run-name` and unique run name:

```bash
set -euo pipefail
RUN_TOKEN="$(date -u +%Y%m%d-%H%M%S)-$$"
RUN_NAME="Build sglang-rdna4 throughput candidate $RUN_TOKEN"
mise exec -- gh workflow run build-sglang-rdna4-throughput.yaml --ref main -f run_token="$RUN_TOKEN"
RUN_ID=''
for _ in {1..30}; do
  MATCHES="$(mise exec -- gh run list --workflow build-sglang-rdna4-throughput.yaml --branch main --event workflow_dispatch --limit 20 --json databaseId,displayTitle | mise exec -- jq -c --arg name "$RUN_NAME" '[.[] | select(.displayTitle == $name)]')"
  MATCH_COUNT="$(mise exec -- jq -r 'length' <<<"$MATCHES")"
  case "$MATCH_COUNT" in
    0) sleep 2 ;;
    1) RUN_ID="$(mise exec -- jq -r 'if length == 1 then .[0].databaseId else error("expected exactly one workflow match") end' <<<"$MATCHES")"; break ;;
    *) printf 'error: ambiguous workflow run title (%s matches)\n' "$MATCH_COUNT" >&2; exit 1 ;;
  esac
done
if [[ -z "$RUN_ID" ]]; then
  printf 'error: workflow run was not found before poll timeout\n' >&2
  exit 1
fi
mise exec -- gh run watch "${RUN_ID}" --exit-status
CANDIDATE_TAG="v0.5.15-gfx1201-decode-ab-086-087-$RUN_TOKEN"
CANDIDATE_DIGEST="$(mise exec -- crane digest "ghcr.io/tanguille/sglang-rdna4:$CANDIDATE_TAG")"
[[ "$CANDIDATE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
CANDIDATE_IMAGE="ghcr.io/tanguille/sglang-rdna4:$CANDIDATE_TAG@$CANDIDATE_DIGEST"
```

Record the digest. The digest is authoritative; use the exact `CANDIDATE_IMAGE` in the candidate GitOps change and script invocation. Do not dispatch the stable workflow or update the production image.

- [ ] **Step 3: Document the GitOps maintenance transition to control**

In one reviewed maintenance change:

1. Add `replicas: 0` to the production `InferenceService` spec in `qwen36-27b-sglang.yaml`.
2. Add `qwen36-27b-decode-ab.yaml` to `models/kustomization.yaml`.
3. Change the `llmkube-models` Kustomization health check in `kubernetes/apps/ai/llmkube/ks.yaml` from the production InferenceService to the control benchmark Deployment in the same GitOps change.
4. Change the benchmark Deployment to `replicas: 1` with the control image and control Triton PVC.
5. Validate, commit, then request explicit push and optional `flux reconcile` approval.

The temporary `healthChecks` entry must be exactly:

```yaml
healthChecks:
  - apiVersion: apps/v1
    kind: Deployment
    name: qwen36-27b-decode-ab
    namespace: ai
```

Keep the existing `healthCheckExprs` entry for `inference.llmkube.dev/v1alpha1` `InferenceService` unchanged. This transition keeps `llmkube-models` and its dependent Kustomizations healthy while production is intentionally `Stopped`.

Wait for production to report `Stopped`, the production pod to disappear, and the benchmark Deployment to become Available. Never use `kubectl scale`, because the operator and Flux own replica state.

- [ ] **Step 4: Add the fixed native benchmark script**

The implementation must provide this interface and safety behavior:

`control` accepts exactly one argument and uses the fixed control image;
`candidate` accepts exactly two arguments and requires the full dynamic
`tag@sha256:digest` matching the per-dispatch tag and lowercase digest. Require
`RUN_LABEL` in `[a-z0-9-]{1,128}` before any cluster call. Before
benchmarking, fetch all matching pods as JSON and require exactly one
non-terminating Running/Ready pod with one `sglang` container, exact image,
command, full ordered benchmark-manifest args, and the arm-specific Triton
PVC. Install an EXIT trap after argument setup; on success or failure it must
preserve the original status and best-effort capture current/previous logs, Pod
YAML/status, describe events, and read-only VRAM evidence. Keep the fixed
matrix and strict JSONL checks unchanged. Use the compatible `--backend sglang`.
Each context runs three independent
one-prompt processes using `--dataset-name random-ids`, `--tokenize-prompt`, the local
tokenizer, `--random-range-ratio 1`, and seed 42. Validate exact `input_lens`,
`output_lens`, and errors for every object, then preserve all three raw objects
and calculate median metrics. The benchmark manifest uses `--tp-size`, not
unsupported `--tp`.

`179000 + 256` remains below the 180000 context limit. SGLang v0.5.15 ignores EOS by default, so every successful result must contain three 256-token outputs. `jq -e` makes any reported error or short output fail the script.

Validate the argument guard and shell syntax:

```bash
test "$(bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh 2>&1; printf '%s' "$?")" = "error: invalid arguments
usage: kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh control | kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh candidate tag@sha256:digest
2"
mise exec -- shellcheck kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh
```

Expected: both commands exit 0.

- [ ] **Step 5: Run the control matrix**

After the benchmark Deployment is Available:

```bash
RUN_LABEL="${RUN_TOKEN}-control-cold" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh control
```

After a fresh approval, change only a Pod-template annotation through reviewed
GitOps, retain the control image and same control PVC, wait for the replacement
Pod Ready, and run the warm matrix with
`RUN_LABEL="${RUN_TOKEN}-control-warm"`. Cold evidence covers startup and an
empty Triton cache; warm evidence covers the populated same-PVC cache.

- [ ] **Step 6: Document the GitOps transition from control to candidate**

First set the benchmark Deployment to `replicas: 0`, push with approval, and wait for the control pod to disappear. The candidate PVC must be new/empty. In the next reviewed change, set the exact `CANDIDATE_IMAGE`, switch the mount to `qwen36-27b-decode-ab-candidate-triton`, and restore benchmark replicas to 1. This two-step transition prevents a single-GPU rollout deadlock even though the Deployment uses `Recreate`.

Wait for a cold candidate boot, then run:

```bash
RUN_LABEL="${RUN_TOKEN}-candidate-cold" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh candidate "${CANDIDATE_IMAGE}"
```

After a fresh approval, retain the candidate image and same candidate PVC while
changing only a Pod-template annotation through reviewed GitOps; wait for the
replacement Pod Ready, then run
`RUN_LABEL="${RUN_TOKEN}-candidate-warm"` with the same exact image. Never
clear PVCs in place. All results are run-tokened and each context has three
independent processes in both cold and warm matrices.

The script guarantees identical flags, seed, lengths, and request counts for both arms. The first boot validates cold Triton compilation; repeated requests in the same pod exercise the warm cache. Do not clear either experiment PVC in place.

- [ ] **Step 7: Document objective comparison gates**

Compare control and candidate TPOT-derived decode tok/s, TTFT, E2E, errors, startup duration, SGLang memory/KV-capacity logs, and `rocm-smi --showmeminfo vram` when that read-only tool exists in the image. The candidate is eligible for a separate promotion decision only if it:

- improves median decode tok/s at 128K or 179K;
- regresses 2K decode by no more than 5%;
- completes every request with no OOM, crash, kernel error, or invalid output;
- retains safe VRAM/KV headroom; and
- repeats consistently across three fixed-seed requests.

Do not run a KV-split override sweep in the primary A/B. If the default result is promising but inconclusive, plan a separate candidate-only sweep for 32/48/64/96 at 128K and 179K.

- [ ] **Step 8: Document GitOps cleanup and production restoration**

In one reviewed cleanup change, remove `qwen36-27b-decode-ab.yaml` from `models/kustomization.yaml`, restore the original explicit `InferenceService/qwen36-27b` health check in `kubernetes/apps/ai/llmkube/ks.yaml`, and restore production to `replicas: 1` (or remove the temporary replicas field). Obtain explicit permission before the push, Flux reconcile, and pruning of the two experiment PVCs. The restored `healthChecks` entry must be exactly:

```yaml
healthChecks:
  - apiVersion: inference.llmkube.dev/v1alpha1
    kind: InferenceService
    name: qwen36-27b
    namespace: ai
```

Retain the existing `InferenceService` `healthCheckExprs` unchanged. Wait until:

```bash
mise exec -- kubectl wait --for=jsonpath='{.status.phase}'=Ready inferenceservice/qwen36-27b -n ai --timeout=35m
mise exec -- kubectl get pods -n ai -l inference.llmkube.dev/service=qwen36-27b
```

Production must be Ready on its original image before closing the maintenance window. Benchmark results never authorize changing the production image digest.

- [ ] **Step 9: Validate and commit the runbook only when requested**

```bash
mise exec -- shellcheck kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh
bash kubernetes/apps/ai/llmkube/models/test-qwen36-decode-ab.sh
git diff --check
```

Then, if requested:

```bash
git add kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.md kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh kubernetes/apps/ai/llmkube/models/test-qwen36-decode-ab.sh
git commit -m "docs(ai): add SGLang decode A/B runbook"
```

### Task 4: Record measured evidence

**Files:**
- Modify: `docs/llm-hosting/sglang-benchmarks.md`

- [ ] **Step 1: Append results only after both matrices and restoration finish**

Add a dated section containing control/candidate image digests, fork refs, model revision, cache state, exact input/output lengths, request count, median TPOT-derived decode tok/s, TTFT, E2E, startup duration, memory evidence, and all errors. Do not prefill values or describe an unrun experiment as measured.

- [ ] **Step 2: State the bounded conclusion**

Record whether the claim was established, limited, or refuted. Separate raw decode findings from production latency and aggregate throughput. If gates fail, retain the production image and document the candidate as rejected.

- [ ] **Step 3: Validate and commit only when requested**

```bash
git diff --check
```

Then, if requested:

```bash
git add docs/llm-hosting/sglang-benchmarks.md
git commit -m "docs(ai): record SGLang decode A/B results"
```

## Verification Evidence Path

The claim is that patches 086 and 087 improve exact-model raw single-stream decode at deep context. Production traffic cannot establish that claim because prompt composition, cache hits, and concurrent load differ. Sequential control/candidate Deployments on the same GPU, fixed model revision, fixed launch flags, separate cold Triton caches, deterministic native benchmark inputs, and JSONL per-request details provide direct evidence. The maintenance and restoration checks bound operational risk; promotion remains a separate explicitly approved change.
