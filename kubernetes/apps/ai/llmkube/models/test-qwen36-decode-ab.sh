#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKTREE=$(CDPATH= cd -- "$SCRIPT_DIR/../../../../../" && pwd)
python3 - "$WORKTREE" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
script = (root / "kubernetes/apps/ai/llmkube/models/run-qwen36-decode-ab.sh").read_text()
runbook = (root / "kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.md").read_text()
plan = (root / "docs/superpowers/plans/2026-07-22-sglang-single-stream-throughput.md").read_text()
spec = (root / "docs/superpowers/specs/2026-07-22-sglang-single-stream-throughput-design.md").read_text()
workflow = (root / ".github/workflows/build-sglang-rdna4-throughput.yaml").read_text()
manifest = (root / "kubernetes/apps/ai/llmkube/models/qwen36-27b-decode-ab.yaml").read_text()
failures = []

def require(condition, name):
    if not condition:
        failures.append(name)

require("jq -e" in script and re.search(r"jq\s+-e[r]", script), "raw jq pod-name extraction")
require("--dataset-name random-ids" in script, "random-ids dataset")
require("--backend sglang" in script and "--backend sglang-oai" not in script, "compatible sglang backend")
require("--tokenize-prompt" in script, "tokenize-prompt")
require("--tokenizer" in script and "/models/1bdc22cc0419b237" in script, "local tokenizer")
require("--random-range-ratio 1" in script, "random range ratio 1")
require(not re.search(r"--dataset-name\s+random(?:\s|[\"'])", script), "no random dataset")
require("--random-range-ratio 0" not in script, "no ratio zero")
require("input_lens" in script and "INPUT_LEN" in script, "input length validation")
require("--num-prompts 1" in script, "one prompt per process")
require(len(re.findall(r"repeat|REPEAT", script)) >= 2, "three independent repeats")
require("RUN_LABEL" in script and "RUN_LABEL" in runbook, "run-tokened results")
require(bool(re.search(r"RUN_LABEL =~ \^\[a-z0-9-\]\{1,128\}\$", script)), "exact 128-character run label regex")
require("mkdir \"$RESULT_DIR\"" in script and "mkdir -p \"$RESULT_DIR\"" not in script, "result directory reuse rejection")
require("pod-selection.json" in script and "PODS_JSON" in script, "selection evidence before validation")
require("non-terminating" in runbook and "phase Running" in runbook and "Ready=True" in runbook, "strict production pod preflight")
require(all(term in runbook for term in ("control-cold", "control-warm", "candidate-cold", "candidate-warm")), "cold and warm runbook labels")
require("same PVC" in runbook or "same-PVC" in runbook, "same PVC warm restart")
require("RUN_TOKEN" in plan and "run-name" in plan and "CANDIDATE_TAG" in plan, "dynamic workflow plan")
require("--tp-size" in plan and not re.search(r"--tp(?:\s|[\"'=])", plan), "supported tp-size plan sample")
require("run_token:" in workflow and "run-name: Build sglang-rdna4 throughput candidate ${{ inputs.run_token }}" in workflow, "actual workflow run token and name")
require("TAG: v0.5.15-gfx1201-decode-ab-086-087-${{ inputs.run_token }}" in workflow and "\n  push:" not in workflow, "actual workflow dynamic tag and no push")
require("--tp-size" in manifest and "HF_HUB_OFFLINE" in manifest and "TRANSFORMERS_OFFLINE" in manifest, "actual manifest offline settings")
require("claimName: qwen36-27b-model-cache" in manifest and re.search(r"mountPath: /models\s+readOnly: true", manifest), "actual model PVC read-only")
require("qwen36-27b-triton-cache" not in manifest and "replicas: 0" in manifest and "type: Recreate" in manifest, "actual dormant isolated benchmark")
require("error: invalid arguments" in plan, "plan argument guard error output")
for text, name in ((plan, "plan"), (runbook, "runbook"), (spec, "spec")):
    require("--dataset-name random-ids" in text or name == "spec", f"{name} random-ids sync")
    require("--random-range-ratio 1" in text or name == "spec", f"{name} ratio sync")
    require("input_lens" in text or name == "spec", f"{name} input validation sync")
require("independent" in plan and "warm" in plan and "cold" in plan, "plan repetition/cache flow")
require("--tp " not in plan, "no unsupported tp sample")

if failures:
    print("RED: contract failures:")
    for failure in failures:
        print(f"- {failure}")
    raise SystemExit(1)
print("GREEN: Task 3 static contract satisfied")
PY

REUSE_LABEL="contract-reuse-$BASHPID"
REUSE_DIR="/tmp/opencode/sglang-decode-ab/$REUSE_LABEL/control"
MARKER="/tmp/opencode/sglang-decode-ab/$REUSE_LABEL/mise-called"
FAKE_BIN="/tmp/opencode/sglang-decode-ab/$REUSE_LABEL/bin"
mkdir -p "$REUSE_DIR" "$FAKE_BIN"
cat >"$FAKE_BIN/mise" <<'FAKE_MISE'
#!/usr/bin/env bash
set -euo pipefail
touch "$MARKER"
exit 77
FAKE_MISE
chmod +x "$FAKE_BIN/mise"
export MARKER
set +e
RUN_LABEL="$REUSE_LABEL" PATH="$FAKE_BIN:$PATH" bash "$SCRIPT_DIR/run-qwen36-decode-ab.sh" control >/dev/null 2>&1
reuse_status=$?
set -e
mise_observed=false
if [[ -e "$MARKER" ]]; then
  mise_observed=true
fi
rm -rf "/tmp/opencode/sglang-decode-ab/$REUSE_LABEL"
if [[ $reuse_status -ne 1 || $mise_observed == true ]]; then
  printf 'RED: result reuse was not rejected before mise (status=%s)\n' "$reuse_status" >&2
  exit 1
fi
printf 'GREEN: result reuse rejected before mise\n'
