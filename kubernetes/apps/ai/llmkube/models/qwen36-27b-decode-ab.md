# Qwen3.6 decode A/B runbook

## Purpose and safety

This compares control and candidate SGLang RDNA4 images at fixed single-stream
context lengths. It causes approved downtime on the only R9700. Every push,
Flux reconcile, resource activation, restart, or deletion requires fresh
explicit approval. No production image change is authorized.

The cluster is GitOps-owned: do not use direct `kubectl scale` or `kubectl
apply`. All live changes below mean reviewed repository changes followed by
separately approved push and Flux reconciliation.

## Prerequisites and preflight

Confirm the worktree contains only reviewed task changes and that the following
read-only checks are safe. These checks capture the production Pod and
InferenceService JSON, then fail closed on any image, generated-argument, or
health drift:

```bash
( set -euo pipefail
  PROD_PODS_JSON="$(mise exec -- kubectl get pods -n ai -l inference.llmkube.dev/service=qwen36-27b -o json)"
  PROD_POD="$(mise exec -- jq -er '
    [.items[] | select(.metadata.deletionTimestamp == null)]
    | if length != 1 then error("expected exactly one non-terminating production pod")
      elif .[0].status.phase != "Running" then error("production pod is not Running")
      elif (any(.[0].status.conditions[]?; .type == "Ready" and .status == "True") | not) then error("production pod is not Ready")
      else .[0].metadata.name end
  ' <<<"$PROD_PODS_JSON")"
  PROD_POD_JSON="$(mise exec -- kubectl get pod "$PROD_POD" -n ai -o json)"
  IS_JSON="$(mise exec -- kubectl get inferenceservice qwen36-27b -n ai -o json)"
  EXPECTED_IMAGE='ghcr.io/tanguille/sglang-rdna4:v0.5.15-gfx1201@sha256:3d1561f6a87ce2d61f7c508f6c2f76fd6bbffb094c99d23cb097a89353355bfe'
  EXPECTED_ARGS='["--model-path","/models/1bdc22cc0419b237/model.safetensors","--host","::","--port","30000","--enable-metrics","--context-length","180000","--mem-fraction-static","0.875","--max-running-requests","32","--chunked-prefill-size","8192","--tp-size","1","--quantization","awq","--kv-cache-dtype","fp8_e4m3","--reasoning-parser","qwen3","--dtype","bfloat16","--num-continuous-decode-steps","16","--watchdog-timeout","600","--attention-backend","triton","--pre-warm-nccl","--trust-remote-code","--chat-template","/opt/rdna4-inference/scripts/qwen3.6_devrole_chat_template.jinja","--tool-call-parser","qwen3_coder","--disable-custom-all-reduce","--disable-overlap-schedule","--cuda-graph-backend-decode=disabled","--cuda-graph-backend-prefill=disabled","--max-mamba-cache-size","60","--served-model-name","qwen-3.6","--mamba-ssm-dtype","bfloat16","--max-queued-requests","32","--weight-loader-drop-cache-after-load","--enable-hierarchical-cache","--hicache-io-backend","direct","--hicache-ratio","1.5","--model-loader-extra-config","{\"num_threads\":2}","--schedule-conservativeness","0.1","--enable-mixed-chunk","--hicache-write-policy","write_through_selective","--host","0.0.0.0"]'

  mise exec -- jq -e --arg expected "$EXPECTED_IMAGE" \
    '.spec.containers | length == 1 and .[0].image == $expected' <<<"$PROD_POD_JSON" >/dev/null
  mise exec -- jq -e --argjson expected "$EXPECTED_ARGS" \
    '.spec.containers | length == 1 and .[0].args == $expected' <<<"$PROD_POD_JSON" >/dev/null
  mise exec -- jq -e \
    'any(.status.conditions[]?; .type == "Ready" and .status == "True")' <<<"$PROD_POD_JSON" >/dev/null
  mise exec -- jq -e \
    '.status.phase == "Ready"
     and any(.status.conditions[]?; .type == "Available" and .status == "True")
     and all(.status.conditions[]?; ((.type == "Failed" or .type == "Degraded") and .status == "True") | not)' \
    <<<"$IS_JSON" >/dev/null
  mise exec -- kubectl exec -n ai "$PROD_POD" -- test -r /models/1bdc22cc0419b237/model.safetensors
  for pvc in qwen36-27b-decode-ab-control-triton qwen36-27b-decode-ab-candidate-triton; do
    PVC_NAME="$(mise exec -- kubectl get pvc -n ai "$pvc" --ignore-not-found -o name)"
    if [[ -n "$PVC_NAME" ]]; then
      printf 'error: experiment PVC already exists: %s\n' "$pvc" >&2
      exit 1
    fi
  done
)
```

The production preflight requires exactly one non-terminating pod in phase Running with Ready=True before extracting its name.

Every assertion aborts the maintenance procedure on missing or unexpected
production pod/model, image or exact argument drift, a Pod without Ready=True,
an InferenceService that is not Ready/Available, Failed or Degraded=True, or
unsafe GPU/memory state. No stop proceeds until the drift or unhealthy state is
corrected and re-reviewed. Record the read-only evidence before requesting
approval. Only a zero exit from this complete subshell permits continuing to the
maintenance transition.

## Build the candidate

After the workflow is merged and a push is approved, generate a unique UTC
token and dispatch the manual workflow with its required run name and input:

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
mise exec -- gh run watch "$RUN_ID" --exit-status
CANDIDATE_TAG="v0.5.15-gfx1201-decode-ab-086-087-$RUN_TOKEN"
CANDIDATE_DIGEST="$(mise exec -- crane digest "ghcr.io/tanguille/sglang-rdna4:$CANDIDATE_TAG")"
[[ "$CANDIDATE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
CANDIDATE_IMAGE="ghcr.io/tanguille/sglang-rdna4:$CANDIDATE_TAG@$CANDIDATE_DIGEST"
printf '%s\n' "$CANDIDATE_IMAGE"
```

The exact unique display title is polled on `main`; an unqualified latest run
is never accepted. The bounded poll must find the exact `RUN_ID`, and the
captured digest is authoritative. Use the resulting `CANDIDATE_IMAGE` in the
candidate GitOps change and script invocation; never use a tag without its
captured digest.

## Maintenance transition and control

Use reviewed GitOps changes, in order:

1. Set production `InferenceService` `replicas: 0`.
2. Add `qwen36-27b-decode-ab.yaml` to `models/kustomization.yaml`.
3. Set benchmark Deployment `replicas: 1`, using the control image and control
   Triton claim.
4. Temporarily change `kubernetes/apps/ai/llmkube/ks.yaml` explicit
   `healthChecks` to:

   ```yaml
   healthChecks:
     - apiVersion: apps/v1
       kind: Deployment
       name: qwen36-27b-decode-ab
       namespace: ai
   ```

   Retain the existing `InferenceService` `healthCheckExprs` unchanged. This
   prevents llmkube-models, litellm, and memini Flux blockage while production
   is intentionally stopped.

Validate before commit; request push/reconcile approval; wait for production
Stopped and its pod gone, and benchmark Available. The fresh control PVC must
be created by this transition. Then run the cold control matrix with the
workflow token:

```bash
RUN_LABEL="${RUN_TOKEN}-control-cold" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh control
```

Results, summaries, server logs, and best-effort VRAM evidence are under the
run-tokened `/tmp/opencode/sglang-decode-ab/${RUN_TOKEN}-control-cold/control/`.

For the warm control matrix, obtain fresh approval for a reviewed GitOps
Recreate restart by changing only a Pod-template annotation while retaining
the control image and the same control PVC. Wait for the replacement Pod to be
Running/Ready, then run:

```bash
RUN_LABEL="${RUN_TOKEN}-control-warm" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh control
```

The cold run proves startup and empty Triton-cache behavior; the warm run
measures the populated same-PVC cache. Never clear a PVC in place.

## Candidate transition and run

Use reviewed GitOps changes to set benchmark replicas to `0`; obtain approved
push/reconcile and wait for the pod to disappear. The candidate PVC must be
empty/new. In the next reviewed change, set the benchmark image to the exact
captured `$CANDIDATE_IMAGE`, switch to the candidate Triton claim, and set
replicas to `1`. Wait for cold boot, then run with that exact image:

```bash
RUN_LABEL="${RUN_TOKEN}-candidate-cold" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh candidate "$CANDIDATE_IMAGE"
```

For the warm candidate matrix, obtain fresh approval for the same reviewed
GitOps Pod-template annotation restart while retaining the candidate image and
the same candidate PVC. Wait for the replacement Pod to be Running/Ready, then
run:

```bash
RUN_LABEL="${RUN_TOKEN}-candidate-warm" bash kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh candidate "$CANDIDATE_IMAGE"
```

No KV split override belongs in the primary A/B. Do not add speculative, MTP,
or EAGLE flags. The script accepts exactly one argument for control and the arm
plus full `tag@sha256:digest` for candidate; it validates the image, pod,
container, command, ordered args, and arm-specific Triton PVC before running.
It always preserves the benchmark exit status while best-effort capturing
current/previous logs, Pod YAML/status, describe output, and VRAM evidence.
Each context uses three independent one-prompt processes with seed 42, and
each run label keeps raw JSONL and summaries from overwriting other matrices.
The runner uses offline `--dataset-name random-ids` with `--tokenize-prompt`, the local
tokenizer, `--random-range-ratio 1`, and validates exact `input_lens` as well
as output lengths and errors.

## Comparison gates

Compare TPOT-derived decode TPS, TTFT, E2E latency, output throughput, startup
duration/logs, SGLang memory/KV-capacity logs, and
`rocm-smi --showmeminfo vram` evidence. Candidate must improve median decode
TPS at 128K or 179K, have no more than a 5% 2K regression, and have no errors,
OOM, crash, kernel, or invalid-output failures. Require safe memory/KV
headroom and consistent three fixed-seed outputs.

Results do not authorize promotion; document them later in
`docs/llm-hosting/sglang-benchmarks.md`.

## Cleanup and rollback

Through reviewed GitOps, remove the benchmark reference, restore production
replicas to `1` (or remove the temporary field), and restore the original
explicit health check exactly:

```yaml
healthChecks:
  - apiVersion: inference.llmkube.dev/v1alpha1
    kind: InferenceService
    name: qwen36-27b
    namespace: ai
```

Keep `healthCheckExprs` unchanged. Obtain explicit push/reconcile/PVC prune
approval. Verify production is Ready on the original image before closing the
window. Never delete or restart resources directly; if unsafe, use reviewed
GitOps to transition back to control and then restore production.
