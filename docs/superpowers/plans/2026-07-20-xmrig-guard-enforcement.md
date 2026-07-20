# XMRig Guard Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing XMRig guard a global, fail-closed gate for the solar-powered XMRig deployment in PR #4035.

**Architecture:** Keep the guard controller read-only and KEDA as the only scaling owner. The existing Prometheus trigger will multiply solar surplus by a strict three-node guard value, return zero for absent/incomplete/stale guard data, and use zero-replica fallback for scaler errors.

**Tech Stack:** Python 3.14 standard library, Kubernetes, KEDA 2.20.1, VictoriaMetrics MetricsQL, FluxCD, Kustomize, yayamlls, flate.

**Branch:** `fix/xmrig-guard-telemetry`

**Worktree:** `/home/tanguille/Documents/PriveProjecten/cluster/.worktrees/xmrig-guard-telemetry`

## Global Constraints

- Preserve the telemetry fixes already committed to PR #4035.
- Keep the guard controller read-only; do not add Kubernetes API writes or RBAC.
- Gate globally: any unsafe, missing, incomplete, stale, or failed guard signal must scale all XMRig replicas to zero.
- Preserve the existing 15-minute solar average, 50 W per-replica target, 25 W activation threshold, 120-second trip dwell, and 600-second recovery dwell.
- Do not fold the unrelated policy-configuration refactor from superseded PR #4008 into this branch.
- Do not push until the user explicitly approves the push.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.

## File map

- `kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py`: verifies the audited enforcement mode and retains controller behavior coverage.
- `kubernetes/apps/web3/xmrig-guard/app/resources/config.json`: selects the audited `enforce` mode.
- `kubernetes/apps/web3/xmrig-guard/app/resources/controller.py`: validates enforcement mode and removes stale observe-only wording while remaining read-only.
- `kubernetes/apps/web3/monero/xmrig/scaledobject.yaml`: consumes the guard signal and owns fail-closed scaling behavior.
- `docs/superpowers/specs/2026-07-20-xmrig-guard-enforcement-design.md`: approved design reference; no implementation changes expected.
- `docs/superpowers/plans/2026-07-20-xmrig-guard-enforcement.md`: tracks execution and is removed after its durable details are reflected in the design/reference documentation.

---

### Task 1: Lock the guard into enforcement mode

**Files:**
- Modify: `kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py`
- Modify: `kubernetes/apps/web3/xmrig-guard/app/resources/config.json`
- Modify: `kubernetes/apps/web3/xmrig-guard/app/resources/controller.py`

**Interfaces:**
- Consumes: `Config.load(dict) -> Config` and the existing exact configuration contract.
- Produces: `Config.mode == "enforce"`; all guard evaluation and metric interfaces remain unchanged.

- [x] **Step 1: Write the failing enforcement-mode test**

Add the mode assertion and explicit observe-mode rejection:

```python
def test_complete_audited_configuration(self):
    cfg = config()
    self.assertEqual(cfg.mode, "enforce")
    self.assertEqual(len(cfg.sensors["control-2"]), 7)
    self.assertEqual(len(cfg.sensors["control-3"]), 8)

def test_observe_mode_is_rejected(self):
    values = config_values()
    values["mode"] = "observe"
    with self.assertRaises(ValueError):
        controller.Config.load(values)
```

Rename `test_observe_never_writes` to `test_enforcement_controller_remains_read_only`; do not change its body.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest discover -s kubernetes/apps/web3/xmrig-guard/app/resources -p 'test_controller.py' -v
```

Expected: `test_complete_audited_configuration` fails because the current configuration still returns `mode == "observe"`.

- [x] **Step 3: Implement the minimal mode change**

In `config.json`:

```json
"mode": "enforce"
```

In `controller.py`, change the module description to `Small, dependency-free XMRig safety signal controller.` and validate the exact enforcement mode:

```python
if parsed.scheme not in ("http", "https") or not parsed.netloc or self.mode != "enforce":
    raise ValueError("only enforce mode and a valid endpoint are supported")
```

- [x] **Step 4: Run the Python tests and verify GREEN**

Run:

```bash
python3 -m unittest discover -s kubernetes/apps/web3/xmrig-guard/app/resources -p 'test_controller.py' -v
python3 -m py_compile kubernetes/apps/web3/xmrig-guard/app/resources/controller.py kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py
```

Expected: all 22 tests pass and bytecode compilation exits zero.

- [x] **Step 5: Commit the enforcement-mode contract**

```bash
git add kubernetes/apps/web3/xmrig-guard/app/resources/config.json kubernetes/apps/web3/xmrig-guard/app/resources/controller.py kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py docs/superpowers/plans/2026-07-20-xmrig-guard-enforcement.md
git commit -m "fix(web3): enable xmrig guard enforcement mode"
```

### Task 2: Gate XMRig scaling on all-node safety

**Files:**
- Modify: `kubernetes/apps/web3/monero/xmrig/scaledobject.yaml`

**Interfaces:**
- Consumes: `xmrig_guard_safe{node="control-1|control-2|control-3"}` scraped by VictoriaMetrics.
- Produces: one composite solar-watt scalar; `0` unless all three fresh guard series equal `1`.

- [x] **Step 1: Replace the observe-only solar trigger with the fail-closed composite query**

Set the final scale-to-zero delay and scaler fallback:

```yaml
spec:
  minReplicaCount: 0
  maxReplicaCount: 3
  cooldownPeriod: 0 # Guard dwell already debounces trips and recovery
  pollingInterval: 60
  fallback:
    failureThreshold: 1
    replicas: 0
```

Replace the trigger query and null behavior while retaining thresholds:

```yaml
query: >-
  max(
    clamp_max(
      clamp_min(-avg_over_time(hass_sensor_power_w{entity="sensor.p1_meter_power",job="homeassistant"}[15m]), 0),
      150
    )
    * on()
    (
      min(xmrig_guard_safe{node=~"control-[123]"})
      * (count(xmrig_guard_safe{node=~"control-[123]"}) == bool 3)
      * (min(timestamp(xmrig_guard_safe{node=~"control-[123]"})) >= bool (time() - 120))
    )
  ) or vector(0)
threshold: "50"
activationThreshold: "25"
ignoreNullValues: "false"
```

Update the nearby comments to explain the global guard, exact node count, 120-second scrape freshness, and zero-replica failure behavior.

- [x] **Step 2: Validate schema and rendered manifests**

Run:

```bash
yayamlls validate kubernetes/apps/web3/monero/xmrig/scaledobject.yaml
mise exec -- flate test all
```

Expected: no error-severity YAML diagnostics and successful HelmRelease/Kustomization rendering.

- [x] **Step 3: Exercise the query contract**

Start a read-only port-forward in a dedicated terminal:

```bash
kubectl -n observability port-forward service/vmsingle-victoria-metrics 18428:8428
```

In another terminal, evaluate the rendered query and three truth-table cases:

```bash
XMRIG_GATE_QUERY="$(yq -r '.spec.triggers[0].metadata.query' kubernetes/apps/web3/monero/xmrig/scaledobject.yaml)"
curl -fsS --get http://127.0.0.1:18428/api/v1/query --data-urlencode "query=${XMRIG_GATE_QUERY}" | jq -e '.status == "success" and (.data.result | length) == 1 and ((.data.result[0].value[1] | tonumber) == 0)'
curl -fsS --get http://127.0.0.1:18428/api/v1/query --data-urlencode 'query=max(vector(73) * on() vector(1)) or vector(0)' | jq -e '.status == "success" and (.data.result | length) == 1 and ((.data.result[0].value[1] | tonumber) == 73)'
curl -fsS --get http://127.0.0.1:18428/api/v1/query --data-urlencode 'query=max(vector(73) * on() vector(0)) or vector(0)' | jq -e '.status == "success" and (.data.result | length) == 1 and ((.data.result[0].value[1] | tonumber) == 0)'
curl -fsS --get http://127.0.0.1:18428/api/v1/query --data-urlencode 'query=max(vector(73) * on() (count(xmrig_guard_safe{node=~"control-[12]"}) == bool 3)) or vector(0)' | jq -e '.status == "success" and (.data.result | length) == 1 and ((.data.result[0].value[1] | tonumber) == 0)'
```

These assertions verify:

- the current unsafe cluster returns exactly one sample with value `0`;
- replacing the guard subexpression with `vector(1)` returns the unchanged capped solar metric;
- replacing it with `vector(0)` returns exactly one zero sample;
- deleting one expected node from the guard selector causes the exact-count term to close the gate.

Stop the port-forward after the assertions. Do not reconcile or mutate the
cluster.

- [x] **Step 4: Commit the active KEDA gate**

```bash
git add kubernetes/apps/web3/monero/xmrig/scaledobject.yaml docs/superpowers/plans/2026-07-20-xmrig-guard-enforcement.md
git commit -m "fix(web3): gate xmrig scaling on thermal safety"
```

### Task 3: Final verification and PR handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-xmrig-guard-enforcement-design.md` only if verification reveals a durable clarification.
- Delete: `docs/superpowers/plans/2026-07-20-xmrig-guard-enforcement.md` after consolidating durable details into the design reference.

**Interfaces:**
- Consumes: completed enforcement-mode and KEDA-gate commits.
- Produces: validated local branch ready to push to PR #4035.

- [ ] **Step 1: Run full scoped validation**

```bash
python3 -m unittest discover -s kubernetes/apps/web3/xmrig-guard/app/resources -p 'test_controller.py' -v
python3 -m py_compile kubernetes/apps/web3/xmrig-guard/app/resources/controller.py kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py
yayamlls validate kubernetes/apps/web3/monero/xmrig/scaledobject.yaml
mise exec -- flate test all
bash .agents/skills/pr-review/scripts/validate-pr.sh
git diff --check origin/main...HEAD
```

Expected: every command exits zero; Python reports 22 passing tests; no diff-check warnings.

- [ ] **Step 2: Review the complete PR diff**

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- kubernetes/apps/web3/xmrig-guard kubernetes/apps/web3/monero/xmrig/scaledobject.yaml docs/superpowers/specs/2026-07-20-xmrig-guard-enforcement-design.md
git status --short --branch
```

Expected: only PR #4035 telemetry fixes, the enforcement gate, its tests, and the durable design reference; clean worktree.

- [ ] **Step 3: Consolidate documentation and remove the temporary plan**

Ensure all durable behavior is present in the design reference, then remove this plan and commit:

```bash
git add docs/superpowers/specs/2026-07-20-xmrig-guard-enforcement-design.md docs/superpowers/plans/2026-07-20-xmrig-guard-enforcement.md
git commit -m "docs(web3): finalize xmrig guard enforcement"
```

- [ ] **Step 4: Request push approval**

Report validation evidence and ask the user for explicit permission to push `fix/xmrig-guard-telemetry`.

- [ ] **Step 5: After approval, push and update PR #4035**

```bash
git push origin fix/xmrig-guard-telemetry
gh pr edit 4035 --repo Tanguille/cluster --title "fix(web3): repair and enforce xmrig thermal guard" --body-file /tmp/xmrig-guard-pr-4035.md
gh pr checks 4035 --repo Tanguille/cluster --watch
```

The PR body must retain the telemetry root causes and validation, replace the observe-only note with the global fail-closed KEDA behavior, document zero-replica fallback/cooldown, and state that PR #4008 was closed as superseded.
