#!/usr/bin/env bash
set -euo pipefail

CONTROL_IMAGE='ghcr.io/tanguille/sglang-rdna4:v0.5.15-gfx1201@sha256:3d1561f6a87ce2d61f7c508f6c2f76fd6bbffb094c99d23cb097a89353355bfe'
CANDIDATE_IMAGE_RE='^ghcr\.io/tanguille/sglang-rdna4:v0\.5\.15-gfx1201-decode-ab-086-087-[a-z0-9-]{1,94}@sha256:[0-9a-f]{64}$'
EXPECTED_ARGS='["--model-path","/models/1bdc22cc0419b237/model.safetensors","--host","0.0.0.0","--port","8000","--enable-metrics","--served-model-name","qwen-3.6","--tp-size","1","--context-length","180000","--mem-fraction-static","0.875","--chunked-prefill-size","8192","--max-running-requests","32","--quantization","awq","--kv-cache-dtype","fp8_e4m3","--reasoning-parser","qwen3","--dtype","bfloat16","--num-continuous-decode-steps","16","--watchdog-timeout","600","--attention-backend","triton","--pre-warm-nccl","--trust-remote-code","--chat-template","/opt/rdna4-inference/scripts/qwen3.6_devrole_chat_template.jinja","--tool-call-parser","qwen3_coder","--disable-custom-all-reduce","--disable-overlap-schedule","--cuda-graph-backend-decode=disabled","--cuda-graph-backend-prefill=disabled","--max-mamba-cache-size","60","--mamba-ssm-dtype","bfloat16","--max-queued-requests","32","--weight-loader-drop-cache-after-load","--enable-hierarchical-cache","--hicache-io-backend","direct","--hicache-ratio","1.5","--model-loader-extra-config","{\"num_threads\":2}","--schedule-conservativeness","0.1","--enable-mixed-chunk","--hicache-write-policy","write_through_selective"]'

usage() {
  printf 'usage: %s control | %s candidate tag@sha256:digest\n' "$0" "$0" >&2
}

if [[ $# -eq 1 && $1 == control ]]; then
  ARM=control
  EXPECTED_IMAGE=$CONTROL_IMAGE
elif [[ $# -eq 2 && $1 == candidate ]]; then
  ARM=candidate
  EXPECTED_IMAGE=$2
  if [[ ! $EXPECTED_IMAGE =~ $CANDIDATE_IMAGE_RE ]]; then
    printf 'error: invalid candidate image\n' >&2
    usage
    exit 2
  fi
else
  printf 'error: invalid arguments\n' >&2
  usage
  exit 2
fi

if [[ -z ${RUN_LABEL:-} || ! $RUN_LABEL =~ ^[a-z0-9-]{1,128}$ ]]; then
  printf 'error: RUN_LABEL must match [a-z0-9-]{1,128}\n' >&2
  exit 2
fi

EXPECTED_CLAIM="qwen36-27b-decode-ab-${ARM}-triton"
RESULT_PARENT="/tmp/opencode/sglang-decode-ab/$RUN_LABEL"
RESULT_DIR="$RESULT_PARENT/$ARM"
POD=''
PODS_JSON=''

capture_evidence() {
  local status=$?
  set +e
  if [[ -n $PODS_JSON ]]; then
    printf '%s\n' "$PODS_JSON" >"$RESULT_DIR/pod-selection.json"
  fi
  if [[ -n $POD ]]; then
    mise exec -- kubectl logs -n ai "$POD" -c sglang >"$RESULT_DIR/server.log" 2>&1
    mise exec -- kubectl logs -n ai "$POD" -c sglang --previous >"$RESULT_DIR/server-previous.log" 2>&1
    mise exec -- kubectl get pod -n ai "$POD" -o yaml >"$RESULT_DIR/pod.yaml" 2>&1
    mise exec -- kubectl get pod -n ai "$POD" -o json | mise exec -- jq '.status' >"$RESULT_DIR/pod-status.json" 2>&1
    mise exec -- kubectl describe pod -n ai "$POD" >"$RESULT_DIR/pod-describe.txt" 2>&1
    mise exec -- kubectl exec -n ai "$POD" -c sglang -- sh -c \
      'if command -v rocm-smi >/dev/null 2>&1; then rocm-smi --showmeminfo vram; else printf "%s\n" "rocm-smi unavailable"; fi' \
      >"$RESULT_DIR/rocm-smi-vram.txt" 2>&1
  fi
  exit "$status"
}
trap capture_evidence EXIT

mkdir -p "$RESULT_PARENT"
if ! mkdir "$RESULT_DIR"; then
  printf 'error: result directory already exists: %s\n' "$RESULT_DIR" >&2
  exit 1
fi
PODS_JSON="$(mise exec -- kubectl get pods -n ai -l app.kubernetes.io/name=qwen36-27b-decode-ab -o json)"
POD="$(mise exec -- jq -er '[.items[] | select(.metadata.deletionTimestamp == null)] | if length == 1 then .[0].metadata.name else empty end' <<<"$PODS_JSON")"
mise exec -- jq -e --arg pod "$POD" --arg expected_image "$EXPECTED_IMAGE" --arg expected_claim "$EXPECTED_CLAIM" --argjson expected_args "$EXPECTED_ARGS" '
  [.items[] | select(.metadata.name == $pod)] as $selected
  | if ($selected | length) != 1 then error("selected benchmark pod disappeared") else $selected[0] end
  | if .status.phase != "Running" then error("benchmark pod is not Running") else . end
  | if (any(.status.conditions[]?; .type == "Ready" and .status == "True") | not) then error("benchmark pod is not Ready") else . end
  | if (.spec.containers | length) != 1 or .spec.containers[0].name != "sglang" then error("expected exactly one sglang container") else . end
  | if .spec.containers[0].image != $expected_image then error("unexpected benchmark image") else . end
  | if .spec.containers[0].command != ["python3", "-m", "sglang.launch_server"] then error("unexpected benchmark command") else . end
  | if .spec.containers[0].args != $expected_args then error("unexpected benchmark args") else . end
  | if ([.spec.volumes[]? | select(.name == "triton-cache") | .persistentVolumeClaim.claimName] != [$expected_claim]) then error("unexpected Triton PVC") else . end
' <<<"$PODS_JSON" >/dev/null

while IFS=: read -r LABEL INPUT_LEN; do
  RAW_FILES=()
  for REPEAT in 1 2 3; do
    REMOTE_RESULT="/tmp/$RUN_LABEL-$ARM-$LABEL-r$REPEAT.jsonl"
    LOCAL_RESULT="$RESULT_DIR/$LABEL.repeat-$REPEAT.jsonl"
    RAW_FILES+=("$LOCAL_RESULT")

    mise exec -- kubectl exec -n ai "$POD" -c sglang -- rm -f "$REMOTE_RESULT"
    mise exec -- kubectl exec -n ai "$POD" -c sglang -- \
      python3 -m sglang.benchmark.serving \
      --backend sglang \
      --base-url http://127.0.0.1:8000 \
      --model qwen-3.6 \
      --dataset-name random-ids \
      --tokenize-prompt \
      --tokenizer /models/1bdc22cc0419b237 \
      --num-prompts 1 \
      --random-input-len "$INPUT_LEN" \
      --random-output-len 256 \
      --random-range-ratio 1 \
      --max-concurrency 1 \
      --request-rate inf \
      --seed 42 \
      --temperature 0 \
      --top-p 1 \
      --cache-report \
      --output-details \
      --output-file "$REMOTE_RESULT"
    mise exec -- kubectl cp "ai/$POD:$REMOTE_RESULT" "$LOCAL_RESULT"
    mise exec -- jq -e -s --argjson input_len "$INPUT_LEN" '
      if length != 1 then error("expected one benchmark result") else .[0] end
      | if .input_lens != [$input_len] then error("unexpected input length") else . end
      | if .output_lens != [256] then error("unexpected output length") else . end
      | if any(.errors[]; . != null and . != "") then error("benchmark request failed") else . end
    ' "$LOCAL_RESULT" >/dev/null
  done

  mise exec -- jq -e -s '
    if length != 3 then error("expected three benchmark repeats") else . end
    | {
        median_decode_tps: (1000 / (sort_by(.median_tpot_ms)[1].median_tpot_ms)),
        median_tpot_ms: sort_by(.median_tpot_ms)[1].median_tpot_ms,
        median_ttft_ms: sort_by(.median_ttft_ms)[1].median_ttft_ms,
        median_e2e_latency_ms: sort_by(.median_e2e_latency_ms)[1].median_e2e_latency_ms,
        output_throughput: sort_by(.output_throughput)[1].output_throughput,
        input_lens: map(.input_lens),
        output_lens: map(.output_lens),
        errors: map(.errors),
        raw_results: .
      }
  ' "${RAW_FILES[@]}" > "$RESULT_DIR/$LABEL.summary.json"
done <<'CASES'
2k:2048
32k:32000
128k:128000
179k:179000
CASES

printf 'results: %s\n' "$RESULT_DIR"
