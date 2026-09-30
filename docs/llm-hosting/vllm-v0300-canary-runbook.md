# vLLM v0.30.0 pin-decision — canary A/B + re-bench runbook

Card: `t_9ffc4f6d`. Cluster: shared R9700 (8x MI355X), namespace `ai`, GitOps via
Flux (`llmkube-models` Kustomization, source `kubernetes/apps/ai/llmkube/ks.yaml`).
**Nothing here may run before Tanguille approves the canary PR** — this is the
execution script for the approved change, not a substitute for it.

## 0. What is being decided

The vLLM v0.30.0 release bumped the TheRock base to **ROCm 10.0** (vllm#55246,
AITER 0.1.21.post2). Per the re-bench policy, any base-layer bump triggers a
re-bench before pinning. This canary A/Bs the pinned v0.30.0 image against the
live nightly on the identical model/checkpoint/args, then re-benches both and
compares.

- Live (baseline): `vllm/vllm-openai-rocm:nightly@sha256:e3fdfb382f2b567718ab6de49a14f5d5695dad84efc6dfd9c38f661b1a763e19`
  (digest re-anchored 2026-09-30: the `nightly` tag rolled forward from the
  original `0b7ab0b2…8df` captured 2026-09-24; the A/B baseline is always
  "what is currently live" so we track the rolling tag)
- Canary (candidate): `vllm/vllm-openai-rocm:v0.30.0@sha256:2e7da1ad1c66836802072588adea75f9f4991da5f9545b4318e91d422c22ce6a`
  (digest resolved from the v0.30.0 tag via the Docker Registry v2 API,
  `check_digests.sh` in this task workspace — tag→digest verified 2026-09-24.)

The canary manifest (`kubernetes/apps/ai/llmkube/models/qwen38-27b-vllm-canary.yaml`)
differs from the live `qwen38-27b-vllm.yaml` by exactly two values — the image
and `metadata.name` — and is line-derived from the live file
(`make_canary.py`, `validate_canary.py` assert the delta and that 24 resources
build with zero name collisions). `served-model-name` stays `qwen-3.8`, so the
`PrometheusRule` in the models dir (`model_name="qwen-3.8"`) and the
`NetworkPolicy` (`inference.llmkube.dev/service: Exists`) cover the canary with
no changes.

## 1. PR (the change that needs approval)

1. `kubernetes/apps/ai/llmkube/models/qwen38-27b-vllm-canary.yaml` — new file.
2. `kubernetes/apps/ai/llmkube/models/kustomization.yaml` — one line
   (`- qwen38-27b-vllm-canary.yaml`) with a paired comment.
3. This runbook — `docs/llm-hosting/vllm-v0300-canary-runbook.md`.

**Do not touch** `qwen38-27b-vllm.yaml` (production stays on the nightly
digest until the decision lands). Park/unpark is a runtime patch, never a git
edit — git keeps `replicas: 1` for both services so a Flux reconcile can never
silently shrink an active service.

## 2. Sequence (each step gated on the previous)

### 2.1 Merge + reconcile
- PR merged → Flux picks up `llmkube-models` → canary InferenceService is
  created. Watch: `flux-operator_get_kubernetes_resources`
  (apiVersion `inference.llmkube.dev/v1alpha1`, kind `InferenceService`,
  name `qwen38-27b-vllm-canary`, namespace `ai`) until the pod is Running and
  ready (model load ~minutes; vLLM warmup on ROCm takes longer).
- Verify the canary pod image: the pod `image` must be the v0.30.0 pinned
  digest (spot-check via the pod spec) — a mismatch means the controller
  ignored the pin.

### 2.2 Warmup (both, identically)
- The W4A16 path is bimodal until warmed (live notes: cold M=2 ~30 vs warm
  ~55 tok/s; "one warmup run is not enough"). Run the warmup pass on each
  service before measuring — same pass, same order, no production traffic.

### 2.3 Bench — canary window
- Park the production IS: `flux-operator_patch_kubernetes_resource`
  merge-patch `{spec.replicas: 0}` on `qwen38-27b-vllm` (CRD allows 0..10,
  verified against `inferenceservices.inference.llmkube.dev`).
- Confirm the nightly pod is fully down before starting (no GPU sharing).
- Port-forward the canary (bench scripts hit `127.0.0.1:<port>`):
  `kubectl -n ai port-forward is/qwen38-27b-vllm-canary <port>:8000`
  (use the service's actual port; confirm with the live IS spec if unsure).
- Run `docs/llm-hosting/bench/longctx.py` and `concsweep.py` against the canary
  (PORT env/arg per script). Record full outputs.
- Park the canary (`replicas: 0`) to release the GPU.

### 2.4 Bench — baseline window
- Unpark production (`replicas: 1`), confirm up + warmed.
- Port-forward `is/qwen38-27b-vllm`, run the identical `longctx.py` +
  `concsweep.py` pass. Record full outputs.

### 2.5 Compare + decide
- Compare warm tok/s (M=1..5), TTFT, and the W4A16 bimodal signature between
  the two windows. Regression beyond noise → keep nightly, drop the pin
  (leave a note on the card). No regression / improvement → the pin is
  cleared for the production rollout.

## 3. Rollout (only after a green comparison)

- In the same repo: change `qwen38-27b-vllm.yaml`'s image to the v0.30.0
  pinned digest (a fresh commit, reviewed the same way). Flux rolls the
  production service.

## 4. Teardown (always, after the decision)

- Delete `qwen38-27b-vllm-canary.yaml` **and** its kustomization line in one
  commit (Flux prunes the orphan). Confirm the canary InferenceService + pod
  are gone from the `ai` namespace.
- Archive the bench outputs on the card (attach) before teardown so the
  comparison is on record.

## 5. Rollback

- Any canary anomaly → delete the canary file + kustomization line (or just
  patch its `replicas: 0` first if the pod is stuck). Production was never
  touched; its nightly digest is intact.

## 6. Known risks

- **Shared GPU, no preemption guard**: the two 27B vLLM tenants share the
  R9700; strict sequential windows are mandatory (this runbook enforces the
  park/confirm-up/confirm-down gates).
- **lds-gate-patch overlay**: carried forward into v0.30.0 (not upstreamed —
  vllm#52619 open); dropping it would re-introduce the W4A16 bimodal
  signature (rocm#6347). The canary keeps it, so the A/B isolates the base
  bump, not the overlay.
- **No spec/args drift**: the canary is line-derived from the live nightly;
  `validate_canary.py` fails loudly on any delta beyond name+image.
