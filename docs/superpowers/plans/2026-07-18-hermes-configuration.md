# Hermes Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate Hermes's premature cron inactivity failures and explicitly
protect its single-PVC rollout, without unvalidated privilege or resource
changes.

**Architecture:** Configure the Hermes app-template HelmRelease as the only
source of truth. A 900-second Hermes scheduler inactivity timeout aligns with
the existing LiteLLM route timeouts, while `Recreate` and pod options make the
single RWO-PVC lifecycle deterministic. Resource limits and PVC size remain
unchanged and are reviewed after a representative 14-day window.

**Tech Stack:** FluxCD, HelmRelease `app-template`, Kubernetes probes and pod
options, Hermes Agent CLI, Prometheus/Grafana, Kopiur-backed Ceph RBD PVC.

---

## File structure

- Modify: `kubernetes/apps/ai/hermes/app/helmrelease.yaml` — Hermes container
  environment, deployment strategy, pod defaults, and readiness timing.
- Reference: `docs/superpowers/specs/2026-07-18-hermes-configuration-design.md`
  — approved scope, evidence, rollout, and rollback boundary.
- Do not modify: `kubernetes/apps/ai/hermes/ks.yaml` — the 10Gi Kopiur-managed
  PVC capacity and UID/GID settings remain appropriate.

### Task 1: Add the bounded HelmRelease settings

**Files:**
- Modify: `kubernetes/apps/ai/hermes/app/helmrelease.yaml:14-79`
- Test: `kubernetes/apps/ai/hermes/app/helmrelease.yaml` queried with `yq`

- [ ] **Step 1: Prove the nine intended manifest assertions are absent**

  Run:

  ```bash
  mise exec -- yq -e '
    .spec.values.controllers.hermes.strategy == "Recreate" and
    .spec.values.defaultPodOptions.terminationGracePeriodSeconds == 90 and
    .spec.values.defaultPodOptions.automountServiceAccountToken == false and
    .spec.values.controllers.hermes.containers.app.env.HERMES_CRON_TIMEOUT == "900" and
    .spec.values.controllers.hermes.containers.app.env.S6_CMD_RECEIVE_SIGNALS == "1" and
    .spec.values.controllers.hermes.containers.app.env.HERMES_RESTART_DRAIN_TIMEOUT == "45" and
    .spec.values.controllers.hermes.containers.app.env.S6_SERVICES_GRACETIME == "80000" and
    .spec.values.controllers.hermes.containers.app.probes.readiness.spec.periodSeconds == 10 and
    .spec.values.controllers.hermes.containers.app.probes.readiness.spec.failureThreshold == 3
  ' kubernetes/apps/ai/hermes/app/helmrelease.yaml
  ```

  Expected: non-zero exit because these settings are not yet present.

- [ ] **Step 2: Add pod lifecycle and token controls**

  Under `spec.values.defaultPodOptions`, retain the existing security context
  and add:

  ```yaml
  automountServiceAccountToken: false
  terminationGracePeriodSeconds: 90
  ```

  Under `spec.values.controllers.hermes`, alongside `annotations`, add:

  ```yaml
  strategy: Recreate
  ```

  Do not add `runAsNonRoot`, `runAsUser`, `readOnlyRootFilesystem`, or
  `capabilities.drop`. The image's s6 entrypoint needs root during initial
  permission setup.

- [ ] **Step 3: Add scheduler and shutdown settings with explicit readiness timing**

  Add these values to `controllers.hermes.containers.app.env` after the
  existing dashboard variables. The 45-second Hermes drain must fit within the
  80-second s6 window, leaving capacity for the documented 5-second timed-agent
  interruption plus adapter/database/state/cron cleanup under Kubernetes' 90-second
  grace period:

  ```yaml
  # Seconds-based scheduler inactivity watchdog; raised from 600 to align with the shortest provider timeout.
  HERMES_CRON_TIMEOUT: "900"
  S6_CMD_RECEIVE_SIGNALS: "1"
  HERMES_RESTART_DRAIN_TIMEOUT: "45"
  S6_SERVICES_GRACETIME: "80000"
  ```

  `S6_SERVICES_GRACETIME` is milliseconds: the 80-second s6 window leaves
  capacity for the documented 5-second timed-agent interruption plus
  adapter/database/state/cron cleanup under the 90-second Kubernetes grace
  period. Keep `HERMES_RESTART_DRAIN_TIMEOUT: "45"`.

  Add the following fields to the existing readiness probe spec, preserving
  `httpGet: *httpGet` and `timeoutSeconds: 5`:

  ```yaml
  periodSeconds: 10
  failureThreshold: 3
  ```

  Do not change LiteLLM provider timeouts, the startup probe, liveness probe,
  resource requests/limits, PVC capacity, or `/dev/shm` sizing.

- [ ] **Step 4: Re-run the nine manifest assertions**

  Run:

  ```bash
  mise exec -- yq -e '
    .spec.values.controllers.hermes.strategy == "Recreate" and
    .spec.values.defaultPodOptions.terminationGracePeriodSeconds == 90 and
    .spec.values.defaultPodOptions.automountServiceAccountToken == false and
    .spec.values.controllers.hermes.containers.app.env.HERMES_CRON_TIMEOUT == "900" and
    .spec.values.controllers.hermes.containers.app.env.S6_CMD_RECEIVE_SIGNALS == "1" and
    .spec.values.controllers.hermes.containers.app.env.HERMES_RESTART_DRAIN_TIMEOUT == "45" and
    .spec.values.controllers.hermes.containers.app.env.S6_SERVICES_GRACETIME == "80000" and
    .spec.values.controllers.hermes.containers.app.probes.readiness.spec.periodSeconds == 10 and
    .spec.values.controllers.hermes.containers.app.probes.readiness.spec.failureThreshold == 3
  ' kubernetes/apps/ai/hermes/app/helmrelease.yaml
  ```

  Expected: exit code 0. The 45-second Hermes drain remains within the
  80-second s6 window, leaving capacity for the documented 5-second timed-agent
  interruption plus adapter/database/state/cron cleanup under the 90-second
  Kubernetes termination grace period.

- [ ] **Step 5: Commit the manifest change**

  ```bash
  git add kubernetes/apps/ai/hermes/app/helmrelease.yaml
  git commit -m "fix(hermes): harden cron and pod lifecycle"
  ```

### Task 2: Validate the GitOps change before rollout

**Files:**
- Verify: `kubernetes/apps/ai/hermes/app/helmrelease.yaml`
- Verify: `kubernetes/apps/ai/hermes/ks.yaml`

- [ ] **Step 1: Inspect the focused diff**

  Run:

  ```bash
  git diff HEAD~1 -- kubernetes/apps/ai/hermes/app/helmrelease.yaml
  git diff --check HEAD~1
  ```

  Expected: only the nine planned settings change, with no whitespace errors.

- [ ] **Step 2: Render and run repository validation**

  Run:

  ```bash
  mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh
  ```

  Expected: successful Flux/Helm rendering and no validation errors. If the
  runner reports an unavailable local tool, record that exact warning and run
  the available renderer rather than treating unavailable tooling as a pass.

- [ ] **Step 3: Verify the rendered app scope remains unchanged**

  Run:

  ```bash
  mise exec -- kustomize build kubernetes/apps/ai/hermes/app
  ```

  Expected: valid YAML output containing the Hermes HelmRelease; no secret is
  decrypted or edited.

- [ ] **Step 4: Commit any validation-only correction separately**

  If validation exposes a chart-schema or rendering problem, make the smallest
  correction limited to `kubernetes/apps/ai/hermes/app/helmrelease.yaml`, rerun
  Steps 1-3, then commit it with:

  ```bash
  git add kubernetes/apps/ai/hermes/app/helmrelease.yaml
  git commit -m "fix(hermes): correct lifecycle rendering"
  ```

  If Steps 1-3 pass unchanged, do not create an empty commit.

### Task 3: Reconcile and verify the controlled replacement

**Files:**
- Modify through Flux: `kubernetes/apps/ai/hermes/app/helmrelease.yaml`
- Inspect: running `ai/hermes` Deployment, Pod, HelmRelease, and Hermes cron
  records

- [ ] **Step 1: Obtain explicit permission for the push and live reconcile**

  State that pushing makes the GitOps change available to Flux and reconciliation
  replaces the single Hermes pod, causing brief dashboard/agent downtime. Include
  the rollback command from Step 5 before running any live command.

- [ ] **Step 2: Reconcile through Flux, not by patching Kubernetes resources**

  After permission, run:

  ```bash
  git push -u origin HEAD
  mise exec -- flux reconcile kustomization hermes --namespace ai --with-source
  mise exec -- kubectl rollout status deployment/hermes --namespace ai --timeout=5m
  ```

  Expected: Flux reports a successful reconcile and exactly one replacement
  Hermes pod becomes Available within five minutes.

- [ ] **Step 3: Check post-rollout health and effective environment**

  Run:

  ```bash
  mise exec -- kubectl get pods --namespace ai -l app.kubernetes.io/name=hermes
  mise exec -- kubectl get helmrelease --namespace ai hermes
  mise exec -- kubectl exec --namespace ai deployment/hermes -- sh -c 'printf "%s\\n" "$HERMES_CRON_TIMEOUT"; hermes cron list'
  ```

  Expected: one Ready pod with zero new restarts, HelmRelease `Ready=True`, the
  printed timeout is `900`, and cron jobs are listed without duplicate active
  entries.

- [ ] **Step 4: Run one previously affected job only with separate approval**

  The existing `kanban-actualizer-main-daily` job ID is
  `e0c97cd8cce8`. It can create or update Kanban content, so obtain explicit
  approval for this functional run. Then run:

  ```bash
  mise exec -- kubectl exec --namespace ai deployment/hermes -- hermes cron run e0c97cd8cce8
  ```

  Expected: the job either completes or reports a real upstream failure; it
  must not terminate with `idle for 600s` or `inactivity timeout`.

- [ ] **Step 5: Roll back if readiness or cron correctness regresses**

  Run:

  ```bash
  # This dedicated branch must contain only this bounded Hermes change.
  git revert --no-edit $(git log --format=%H --grep='^fix(hermes):' origin/main..HEAD)
  git push
  mise exec -- flux reconcile kustomization hermes --namespace ai --with-source
  ```

  Expected: Flux restores the preceding HelmRelease configuration. The branch
  must contain only this bounded Hermes change before running the revert. Ask
  for explicit permission before the push and reconcile; do not force-push.

### Task 4: Retain resources and schedule the evidence-based review

**Files:**
- Verify: `kubernetes/apps/ai/hermes/app/helmrelease.yaml:73-79`
- Reference: `docs/superpowers/specs/2026-07-18-hermes-configuration-design.md:47-53`

- [ ] **Step 1: Confirm no resource or storage values changed**

  Run:

  ```bash
  mise exec -- yq -e '
    .spec.values.controllers.hermes.containers.app.resources.requests.cpu == "100m" and
    .spec.values.controllers.hermes.containers.app.resources.requests.memory == "2Gi" and
    .spec.values.controllers.hermes.containers.app.resources.limits.cpu == 2 and
    .spec.values.controllers.hermes.containers.app.resources.limits.memory == "4Gi"
  ' kubernetes/apps/ai/hermes/app/helmrelease.yaml
  ```

  Expected: exit code 0.

- [ ] **Step 2: Record the fourteen-day review criteria in the pull request**

  Add this checklist to the PR description:

  ```markdown
  - [ ] 14-day CPU 5-minute peak remains at or above 389m before raising the CPU request to 500m.
  - [ ] Memory p95/max, restarts, and OOMKilled remain within the current 2Gi/4Gi envelope.
  - [ ] CFS throttling ratio remains low and does not correlate with failed cron jobs.
  - [ ] PVC use remains below 80% of 10Gi.
  ```

  Expected: no resource or PVC resize is proposed before this evidence exists.
