# XMRig Guard Enforcement Design

## Goal

Turn the existing XMRig guard from an observe-only metric producer into a
global, fail-closed safety gate for the solar-powered XMRig deployment. If any
audited node is unsafe, or the guard signal cannot be trusted, all miners must
scale to zero. Mining may resume only after every audited node completes the
existing recovery dwell and solar surplus still supports it.

## Architecture

Keep KEDA as the only scaling controller. The guard remains a read-only
telemetry evaluator and publishes `xmrig_guard_safe{node=...}` for the three
audited nodes. The existing solar Prometheus trigger consumes a single
composite VictoriaMetrics query:

1. Calculate the existing capped solar-surplus value.
2. Calculate a guard value that is `1` only when exactly three expected node
   series exist, every value is `1`, and the oldest sample is no more than 120
   seconds old.
3. Multiply solar surplus by the guard value.
4. Return one scalar `0` when guard data is absent or incomplete.

This preserves the current 50 W per replica and 25 W activation thresholds
when safe. An unsafe gate produces zero regardless of available solar power.

## Failure and timing behavior

- Guard query/evaluation errors already invalidate that node and publish
  `safe=0`.
- The KEDA Prometheus scaler must use `ignoreNullValues: "false"`.
- KEDA fallback must use one failure and zero replicas so a VictoriaMetrics or
  scaler error also fails closed.
- Set KEDA's scale-to-zero cooldown to zero. The guard already debounces trips
  for 120 seconds and recovery for 600 seconds; a second cooldown would only
  delay protection.
- Preserve the existing scale-up behavior and solar averaging.
- Change the audited guard mode from `observe` to `enforce` and remove stale
  observe-only wording. The controller receives no Kubernetes write RBAC; KEDA
  performs enforcement through the existing ScaledObject.

## Scope

Modify only the guard configuration/controller/tests and the existing XMRig
ScaledObject. Do not add node labels, taints, eviction logic, Kubernetes API
writes, a second autoscaler, or the unrelated policy-configuration refactor
from superseded PR #4008.

## Verification

- Add the enforcement-mode test first and confirm it fails before changing the
  controller/configuration.
- Run the complete Python unit-test suite and bytecode compilation.
- Validate the ScaledObject with `yayamlls` and render the affected manifests.
- Exercise the composite query against VictoriaMetrics for current unsafe
  data and synthetic truth-table variants where practical.
- Run repository diff checks and the scoped PR validation workflow.
- Update PR #4035's title and body to cover telemetry repair and active global
  enforcement.

## Implementation verification

- The enforcement-mode test failed against `observe` before the production
  change, then all 22 controller tests passed in `enforce` mode.
- Python bytecode compilation and KEDA schema validation passed.
- `flate test all` rendered all 276 resources successfully; its two warnings
  are pre-existing unused chart values outside this change.
- The live composite MetricsQL returned exactly one zero sample while the
  cluster was unsafe. Synthetic open, closed, and missing-node assertions
  returned the expected `73`, `0`, and `0` values.
- The repository-wide PR validator's manifest phase passed. Its only error is
  a pre-existing ShellCheck SC2164 warning in the unchanged
  `sglang-env-rebuild.sh`, reproduced byte-for-byte from `origin/main`; this PR
  changes no shell scripts.
