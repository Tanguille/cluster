# Hermes configuration reliability and hardening

## Goal

Improve Hermes scheduled-job reliability and make the singleton deployment's
rollout behavior explicit, without speculative resource reductions or changes
that could break its root-based initialization.

## Evidence

- Two agent cron jobs failed after the Hermes scheduler's 600-second inactivity
  watchdog elapsed while waiting for a non-streaming model response.
- The relevant LiteLLM routes allow 900 and 1800 seconds, so the cron watchdog
  is the limiting timeout.
- Hermes had no restarts or OOM events. Seven-day peaks were about 389m CPU and
  1.86 GiB memory; the PVC is 2.61 GiB used of 9.75 GiB.
- Hermes uses one RWO PVC and an image that initializes as root before dropping
  gateway privileges.

## Changes

### Reliability

Set `HERMES_CRON_TIMEOUT: "900"` on the Hermes application container. This
seconds-based scheduler inactivity watchdog is raised from the default 600 to
align with the shortest provider timeout, preserving detection of truly hung
jobs. Do not set it to unlimited or alter provider timeouts without new
evidence.

### Lifecycle and least privilege

In the HelmRelease values:

- Set the Hermes controller rollout strategy to `Recreate` so a rollout cannot
  overlap pods against the RWO PVC.
- Set `terminationGracePeriodSeconds: 90` in `defaultPodOptions` so the gateway
  can stop an in-flight request cleanly.
- Set `S6_CMD_RECEIVE_SIGNALS: "1"` so s6 forwards termination signals to the
  gateway, and retain `HERMES_RESTART_DRAIN_TIMEOUT: "45"` so Hermes drains
  in-flight work before the shutdown budget expires.
- Set `S6_SERVICES_GRACETIME: "80000"` (milliseconds) so s6 allows the service
  drain to complete. This leaves capacity for the documented 5-second
  timed-agent interruption plus adapter, database, state, and cron cleanup
  within the 80-second s6 window, under Kubernetes' 90-second grace period.
- Set `automountServiceAccountToken: false` in `defaultPodOptions`; Hermes has
  no declared Kubernetes API dependency.
- Make the existing `/api/status` readiness probe timing explicit with a
  10-second period and three failures before it is considered unready. Retain
  the startup probe as the primary guard for slow initialisation.

Do not set `runAsNonRoot`, a read-only root filesystem, or capability drops in
this change. Those controls require an image-level test because s6 starts as
root and may write outside the mounted data and shared-memory paths.

### Resources and storage

Keep the current `100m` CPU / `2Gi` memory request and `2` CPU / `4Gi` limits,
and retain the 10Gi PVC. The measured data does not justify an immediate
change. Review fourteen days of representative activity before increasing only
the CPU request to `500m`; make that change only if the historic burst remains
representative. Do not reduce memory or PVC capacity.

## Rollout and verification

1. Render and validate the HelmRelease with the repository validation script.
2. Reconcile through Flux only; do not directly patch the Deployment.
3. Confirm a single replacement pod reaches Ready and the dashboard gateway is
   healthy.
4. Run one previously affected scheduled job with explicit approval. Confirm it
   can remain inactive beyond 600 seconds and ends with completion or a real
   upstream error, not a scheduler inactivity timeout.
5. Check for duplicate executions, restarts, OOM kills, readiness failures, and
   PVC errors after the rollout.
6. Monitor CPU, memory, throttling, and PVC use for fourteen days before a
   resource decision.

## Rollback

From a dedicated branch containing only this bounded Hermes change, revert all
current Hermes fix commits newest-first, then push and let Flux reconcile:

```bash
git revert --no-edit $(git log --format=%H --grep='^fix(hermes):' origin/main..HEAD)
git push
mise exec -- flux reconcile kustomization hermes --namespace ai --with-source
```

The branch must contain only this bounded Hermes change before running the
command. This restores the prior shutdown and lifecycle defaults; no data
migration or PVC rollback is required.

## Non-goals

- Changing model/provider request timeouts without authenticated job evidence.
- Adding a PodDisruptionBudget or ServiceMonitor without a multi-replica design
  or an exposed Prometheus metrics endpoint.
- Applying direct live-cluster edits outside Flux.
