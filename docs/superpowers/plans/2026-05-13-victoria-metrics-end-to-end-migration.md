# VictoriaMetrics Remaining Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining VictoriaMetrics migration by cutting queries over, backfilling historical Prometheus data, cutting alerting over, and decommissioning kube-prometheus-stack safely.

**Architecture:** Keep kube-prometheus-stack online until VictoriaMetrics has current queries, historical data, production alerting, and a soak window verified. PR #2903 remains the query-consumer cutover; historical backfill is a separate gated operation using a Prometheus TSDB snapshot plus `vmctl prometheus`.

**Tech Stack:** FluxCD, Kustomize, kubeconform, kube-prometheus-stack, VictoriaMetrics K8s Stack `0.78.0`, VMSingle, VMAgent, VMAlert, VMAlertmanager, Grafana Operator, Prometheus Operator CRDs, Rook-Ceph `ceph-block`, `vmctl`.

---

## Migration phases

| Phase | Vehicle | Scope | Gate |
| --- | --- | --- | --- |
| 1 | PR #2903 | Query/consumer cutover to VMSingle and Grafana selector fix | Existing Prometheus query URL absent from Kubernetes YAML |
| 2 | New PR | Move shared monitoring resources out of kube-prometheus-stack ownership and add backfill runbook | kube-prometheus-stack still reconciles; moved resources render |
| 3 | Approved live operation | Snapshot Prometheus TSDB and import history with `vmctl prometheus` | Historical VMSingle queries return pre-cutover data |
| 4 | New PR | Cut production alerting to VMAlertmanager | Synthetic critical alert reaches Discord exactly once |
| 5 | New PR | Soft decommission kube-prometheus-stack routes and soak | No user-facing consumers use old Prometheus/Alertmanager |
| 6 | New PR | Hard decommission kube-prometheus-stack | No required KPS-owned resources or CRDs remain |

## Invariants

- Do **not** run historical backfill as a side effect of merging PR #2903.
- Use `vmctl prometheus` with a Prometheus snapshot; use remote-read only if snapshot access is impossible.
- Do **not** mount the live Prometheus RWO PVC into another pod while Prometheus is running.
- Do **not** repoint alerting tools to VMAlertmanager while it is still blackhole-only.
- Do **not** delete kube-prometheus-stack before moving its dashboards, `AlertmanagerConfig`, `ScrapeConfig`, and SOPS secret out of its app path.
- Ask before live reconciles, live snapshot/import operations, decrypting/editing SOPS secrets, or pushing.

## Current state and decisions to preserve

PR #2903 already contains the query cutover and the fix for the Grafana Operator selector mismatch. Keep that PR focused; do not add a `vmctl` Job or any backfill runtime manifest to it.

**What PR #2903 changes:**

- Canonical Grafana datasource `name: prometheus` / `uid: prometheus` now points to `http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428`.
- Direct metrics consumers now point to VMSingle: Kromgo, Rook Ceph dashboard metrics, KEDA `nfs-scaler`, and KEDA `xmrig`.
- Grafana CR now has `dashboards: grafana`, because the VictoriaMetrics chart merges that default into generated `GrafanaDatasource` and `GrafanaDashboard` selectors.
- CNPG performance docs use the canonical `prometheus` Grafana datasource backed by VictoriaMetrics and direct VMSingle port-forward examples.

**What stays on kube-prometheus-stack for now:**

- Production Alertmanager routing and the Discord receiver remain in `kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml` and `secret.sops.yaml`.
- Grafana Alertmanager datasource remains `http://alertmanager-operated.observability.svc.cluster.local:9093`.
- `silence-operator` remains `http://kube-prometheus-stack-alertmanager:9093`.
- `siren` remains `http://kube-prometheus-stack-alertmanager.observability.svc.cluster.local:9093`.
- kube-prometheus-stack still owns shared dashboards, `ScrapeConfig`, `AlertmanagerConfig`, and the SOPS secret until Phase 2 moves them.

**Why backfill is separate:**

- VMAgent has been scraping current data into VMSingle since the VictoriaMetrics stack went live, so backfill must stop before the side-by-side start boundary to avoid overlapping recent samples.
- Use `--prom-filter-time-end=2026-05-12T21:20:00Z` as the initial no-overlap cutoff unless live evidence chooses a better timestamp.
- Prometheus uses an RWO `ceph-block` PVC. Mounting that live PVC into a second pod risks multi-attach problems and TSDB corruption. Use a Prometheus snapshot plus a storage-level clone, or stop Prometheus during an approved maintenance window.
- The Prometheus admin snapshot API may require `prometheus.prometheusSpec.enableAdminAPI: true`; that must be temporary and approved.

**Why decommission is last:**

- Removing kube-prometheus-stack too early can prune useful resources and remove CRDs still backing `ServiceMonitor`, `PodMonitor`, `PrometheusRule`, `Probe`, `ScrapeConfig`, or `AlertmanagerConfig` resources.
- VMAlertmanager is blackhole-only until Phase 4. Repointing alerting consumers before migrating routing would silently drop notifications.

## Phase 1: Finish current PR #2903 without adding backfill

**Files already in PR #2903:**
- `docs/database/postgres-cnpg-performance-check.md`
- `kubernetes/apps/observability/grafana/instance/grafana.yaml`
- `kubernetes/apps/observability/grafana/instance/grafanadatasource.yaml`
- `kubernetes/apps/observability/kromgo/app/helmrelease.yaml`
- `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`
- `kubernetes/apps/web3/monero/xmrig/scaledobject.yaml`
- `kubernetes/components/nfs-scaler/scaledobject.yaml`

- [ ] **Step 1: Keep PR #2903 scoped to query cutover**

Verify the PR does not introduce a backfill Job or one-shot runtime object:

```bash
BASE=$(git merge-base HEAD origin/main)
git diff --name-only "$BASE"...HEAD
git grep -nE 'vmctl|prom-snapshot|remote-read|VolumeSnapshot|prometheus-backfill' -- 'kubernetes/**/*.yaml' || true
```

Expected: changed files are the seven files listed above plus this plan if included; the grep command has no Kubernetes YAML matches.

- [ ] **Step 2: Update PR #2903 description**

Add this note to PR #2903:

```markdown
Historical Prometheus backfill is intentionally deferred. It will be handled in the next gated phase using a Prometheus TSDB snapshot plus `vmctl prometheus`; it is not safe to run as an automatic side effect of this query-consumer cutover PR. Alerting cutover and kube-prometheus-stack decommissioning are also out of scope here.
```

- [ ] **Step 3: Validate PR #2903 locally**

```bash
git diff --check "$BASE"...HEAD
for path in \
  kubernetes/apps/observability/grafana/instance \
  kubernetes/apps/observability/kromgo/app \
  kubernetes/apps/web3/monero/xmrig \
  kubernetes/apps/rook-ceph/rook-ceph/cluster; do
  /home/tanguille/.local/bin/mise exec -- kustomize build "$path" \
    | /home/tanguille/.local/bin/mise exec -- kubeconform -strict -ignore-missing-schemas -skip Secret
done
/home/tanguille/.local/bin/mise exec -- sh -c 'label=$(kustomize build kubernetes/apps/observability/grafana/instance | yq -r "select(.kind == \"Grafana\" and .metadata.name == \"grafana\") | .metadata.labels.dashboards // \"<missing>\""); test "$label" = grafana; printf "grafana dashboards label=%s\n" "$label"'
if git grep -n 'http://prometheus-operated.observability.svc.cluster.local:9090' -- 'kubernetes/**/*.yaml'; then exit 1; else printf 'old prometheus url absent from kubernetes yaml\n'; fi
```

Expected: command exits `0`, Grafana renders `dashboards: grafana`, and the old Prometheus query URL is absent.

## Phase 2: Move shared resources out of kube-prometheus-stack ownership

**Files:**
- Create: `kubernetes/apps/observability/monitoring-resources/ks.yaml`
- Create: `kubernetes/apps/observability/monitoring-resources/app/kustomization.yaml`
- Move: `kubernetes/apps/observability/kube-prometheus-stack/app/grafanadashboard/`
- Move: `kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml`
- Move: `kubernetes/apps/observability/kube-prometheus-stack/app/scrapeconfig.yaml`
- Move: `kubernetes/apps/observability/kube-prometheus-stack/app/secret.sops.yaml`
- Modify: `kubernetes/apps/observability/kube-prometheus-stack/app/kustomization.yaml`
- Modify: `kubernetes/apps/observability/kustomization.yaml`

- [ ] **Step 1: Move resources without decrypting secrets**

```bash
mkdir -p kubernetes/apps/observability/monitoring-resources/app
git mv kubernetes/apps/observability/kube-prometheus-stack/app/grafanadashboard kubernetes/apps/observability/monitoring-resources/app/grafanadashboard
git mv kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml kubernetes/apps/observability/monitoring-resources/app/alertmanagerconfig.yaml
git mv kubernetes/apps/observability/kube-prometheus-stack/app/scrapeconfig.yaml kubernetes/apps/observability/monitoring-resources/app/scrapeconfig.yaml
git mv kubernetes/apps/observability/kube-prometheus-stack/app/secret.sops.yaml kubernetes/apps/observability/monitoring-resources/app/secret.sops.yaml
```

- [ ] **Step 2: Add `monitoring-resources` Flux Kustomization**

Create `kubernetes/apps/observability/monitoring-resources/ks.yaml`:

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: monitoring-resources
  namespace: &namespace observability
spec:
  dependsOn:
    - name: grafana-instance
      namespace: observability
    - name: kube-prometheus-stack
      namespace: observability
  interval: 1h
  path: ./kubernetes/apps/observability/monitoring-resources/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  targetNamespace: *namespace
  wait: false
```

Create `kubernetes/apps/observability/monitoring-resources/app/kustomization.yaml`:

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - grafanadashboard

  - alertmanagerconfig.yaml
  - scrapeconfig.yaml
  - secret.sops.yaml
```

- [ ] **Step 3: Remove moved resources from kube-prometheus-stack app**

Replace `kubernetes/apps/observability/kube-prometheus-stack/app/kustomization.yaml` with:

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - ocirepository.yaml
```

Add `./monitoring-resources/ks.yaml` to `kubernetes/apps/observability/kustomization.yaml` before `./kube-prometheus-stack/ks.yaml`.

- [ ] **Step 4: Validate and commit**

```bash
/home/tanguille/.local/bin/mise exec -- kustomize build kubernetes/apps/observability/monitoring-resources/app \
  | /home/tanguille/.local/bin/mise exec -- kubeconform -strict -ignore-missing-schemas -skip Secret
/home/tanguille/.local/bin/mise exec -- kustomize build kubernetes/apps/observability/kube-prometheus-stack/app \
  | /home/tanguille/.local/bin/mise exec -- kubeconform -strict -ignore-missing-schemas -skip Secret
git add kubernetes/apps/observability
git commit -m "refactor(observability): decouple monitoring resources from kube-prometheus-stack"
```

## Phase 3: Add and execute Prometheus historical backfill runbook

**Files:**
- Create: `docs/runbooks/victoria-metrics-prometheus-backfill.md`
- Temporarily modify during approved maintenance: `kubernetes/apps/observability/kube-prometheus-stack/app/helmrelease.yaml`

- [ ] **Step 1: Add the runbook**

Create `docs/runbooks/victoria-metrics-prometheus-backfill.md` with these concrete rules and commands:

````markdown
# VictoriaMetrics Prometheus Historical Backfill Runbook

## Rules

- Use `vmctl prometheus` against a Prometheus TSDB snapshot.
- Do not mount the live Prometheus RWO PVC into another pod while Prometheus is running.
- Prefer a Rook-Ceph CSI `VolumeSnapshot` and clone of the Prometheus PVC.
- Enable Prometheus admin API only for the snapshot maintenance window.
- Use `--prom-filter-time-end=2026-05-12T21:20:00Z` unless live evidence chooses a better no-overlap boundary.

## Preflight

```bash
export K="/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig"
$K kubectl -n observability get pods,pvc | grep -E 'prometheus|vmsingle'
$K kubectl get volumesnapshotclass
$K kubectl -n observability get svc prometheus-operated vmsingle-victoria-metrics
```

## Snapshot

Temporarily set `enableAdminAPI: true` under `prometheus.prometheusSpec`, reconcile with approval, then:

```bash
$K kubectl -n observability port-forward svc/prometheus-operated 9090:9090
curl -XPOST 'http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot'
```

Record the returned snapshot name.

## Clone Prometheus PVC

Create a `VolumeSnapshot` from the Prometheus PVC and a `prometheus-vm-backfill-clone` PVC from that snapshot. Validate with `kubectl apply --dry-run=server` before applying.

## Narrow import

Run a one-off Job using image `victoriametrics/vmctl:v1.143.0`:

```bash
vmctl prometheus -s \
  --disable-progress-bar \
  --prom-snapshot=/prometheus/snapshots/<snapshot-name> \
  --prom-concurrency=2 \
  --vm-concurrency=1 \
  --prom-filter-time-end=2026-05-12T21:20:00Z \
  --prom-filter-label=__name__ \
  --prom-filter-label-value=up \
  --vm-addr=http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428
```

## Full import

After narrow import validates, rerun without the label filters and with `--prom-concurrency=4 --vm-concurrency=2`.

## Validate

```bash
$K kubectl -n observability port-forward svc/vmsingle-victoria-metrics 8428:8428
curl -G 'http://127.0.0.1:8428/api/v1/query_range' \
  --data-urlencode 'query=up' \
  --data-urlencode 'start=2026-05-01T00:00:00Z' \
  --data-urlencode 'end=2026-05-01T01:00:00Z' \
  --data-urlencode 'step=1m'
```

Expected: `"status":"success"` and non-empty historical data for ranges retained by old Prometheus.

## Cleanup

Remove `enableAdminAPI: true`, delete vmctl Jobs after saving logs, then delete the clone PVC and VolumeSnapshot after validation passes.
````

- [ ] **Step 2: Commit the runbook**

```bash
git add docs/runbooks/victoria-metrics-prometheus-backfill.md
git commit -m "docs(observability): add victoria-metrics backfill runbook"
```

- [ ] **Step 3: Execute live backfill only after explicit approval**

Follow the runbook. The gate to proceed to alerting cutover is:

```text
vmctl narrow import completed
vmctl full import completed
VMSingle historical query_range returns expected pre-cutover data
Prometheus admin API disabled again
temporary import resources removed or intentionally retained for investigation
```

## Phase 4: Cut production alerting to VMAlertmanager

**Files:**
- `kubernetes/apps/observability/victoria-metrics/app/helmrelease.yaml`
- `kubernetes/apps/observability/grafana/instance/grafanadatasource.yaml`
- `kubernetes/apps/observability/silence-operator/app/helmrelease.yaml`
- `kubernetes/apps/observability/siren/app/helmrelease.yaml`

- [ ] **Step 1: Migrate production routing to VMAlertmanager**

In `victoria-metrics/app/helmrelease.yaml`:

- Keep `alertmanager.useManagedConfig: false`.
- Replace the blackhole-only config with the same route semantics as `monitoring-resources/app/alertmanagerconfig.yaml`: default receiver `discord`, blackhole only `InfoInhibitor`, critical alerts to Discord, warning inhibited by matching critical alerts.
- Mount existing `alertmanager-secret` via `alertmanager.spec.secrets` and use `webhook_url_file: /etc/vm/secrets/alertmanager-secret/DISCORD_WEBHOOK_URL` in the Discord receiver.

- [ ] **Step 2: Repoint alerting consumers**

Change only these URLs:

```yaml
# grafana/instance/grafanadatasource.yaml
url: http://vmalertmanager-victoria-metrics.observability.svc.cluster.local:9093

# silence-operator/app/helmrelease.yaml
alertmanagerAddress: http://vmalertmanager-victoria-metrics:9093

# siren/app/helmrelease.yaml
ALERTMANAGER_BASE_URL: http://vmalertmanager-victoria-metrics.observability.svc.cluster.local:9093
```

- [ ] **Step 3: Validate and test**

```bash
for path in \
  kubernetes/apps/observability/victoria-metrics/app \
  kubernetes/apps/observability/grafana/instance \
  kubernetes/apps/observability/silence-operator/app \
  kubernetes/apps/observability/siren/app; do
  /home/tanguille/.local/bin/mise exec -- kustomize build "$path" \
    | /home/tanguille/.local/bin/mise exec -- kubeconform -strict -ignore-missing-schemas -skip Secret
done
```

After approved merge/reconcile, send one synthetic critical alert to VMAlertmanager and confirm exactly one Discord notification is delivered.

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/observability/victoria-metrics/app/helmrelease.yaml \
  kubernetes/apps/observability/grafana/instance/grafanadatasource.yaml \
  kubernetes/apps/observability/silence-operator/app/helmrelease.yaml \
  kubernetes/apps/observability/siren/app/helmrelease.yaml
git commit -m "feat(observability): cut over alerting to victoria-metrics"
```

## Phase 5: Soft decommission kube-prometheus-stack

**Files:**
- `kubernetes/apps/observability/kube-prometheus-stack/app/helmrelease.yaml`
- `docs/runbooks/victoria-metrics-prometheus-backfill.md`

- [ ] **Step 1: Disable old user-facing routes, keep rollback pods**

Set both route blocks to disabled:

```yaml
alertmanager:
  route:
    main:
      enabled: false
prometheus:
  route:
    main:
      enabled: false
```

Do not remove Prometheus, Alertmanager, or the Prometheus Operator yet.

- [ ] **Step 2: Verify consumers no longer use old endpoints**

```bash
if git grep -nE 'prometheus-operated.observability.svc.cluster.local:9090|kube-prometheus-stack-alertmanager|alertmanager-operated.observability.svc.cluster.local:9093' -- 'kubernetes/**/*.yaml'; then exit 1; fi
```

Expected: no matches.

- [ ] **Step 3: Soak for at least 7 days**

During soak, verify:

- Grafana dashboards render from canonical datasource `prometheus`, backed by VMSingle.
- KEDA, Kromgo, and Rook Ceph continue to work.
- Historical queries return backfilled data.
- VMAlertmanager sends production notifications exactly once.

## Phase 6: Hard decommission kube-prometheus-stack

**Files:**
- `kubernetes/apps/observability/kustomization.yaml`
- `kubernetes/apps/observability/victoria-metrics/ks.yaml`
- `kubernetes/apps/observability/kube-prometheus-stack/**`

- [ ] **Step 1: Confirm remaining Prometheus Operator CRD ownership**

```bash
git grep -nE 'apiVersion: monitoring.coreos.com|kind: (ServiceMonitor|PodMonitor|PrometheusRule|Probe|ScrapeConfig|AlertmanagerConfig)' -- 'kubernetes/**/*.yaml'
```

Expected: each remaining resource is either intentionally supported by a CRD owner that remains installed, or converted to the VictoriaMetrics equivalent before KPS deletion.

- [ ] **Step 2: Remove KPS dependency and tree reference**

In `kubernetes/apps/observability/victoria-metrics/ks.yaml`, remove the `dependsOn` entry for `kube-prometheus-stack`.

In `kubernetes/apps/observability/kustomization.yaml`, remove:

```yaml
  - ./kube-prometheus-stack/ks.yaml
```

- [ ] **Step 3: Delete kube-prometheus-stack app shell**

```bash
git rm -r kubernetes/apps/observability/kube-prometheus-stack
```

This must not delete dashboards, AlertmanagerConfig, ScrapeConfig, or `alertmanager-secret`, because those were moved in Phase 2.

- [ ] **Step 4: Validate and commit**

```bash
/home/tanguille/.local/bin/mise exec -- kustomize build kubernetes/apps/observability \
  | /home/tanguille/.local/bin/mise exec -- kubeconform -strict -ignore-missing-schemas -skip Secret
git add kubernetes/apps/observability
git commit -m "chore(observability): decommission kube-prometheus-stack"
```
