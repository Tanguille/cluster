# Paperclip (AI agent orchestration) — Design

- Date: 2026-09-29
- Status: approved (§1–§5, namespace revised to `ai`)
- Target: `paperclipinc/paperclip-operator` (`oci://ghcr.io/paperclipinc/charts/paperclip-operator:0.19.1`), CRD `Instance paperclip.inc/v1alpha1`
- Scope: v1 = Approach A (operator + managed DB, private). Production external-CNPG + public (B) deferred. app-template direct (C) fallback only.

## 1. Architecture

`kubernetes/apps/ai/paperclip/app/` with:

- `ocirepository.yaml` → `helmrelease.yaml` (operator, `crds.install: true`) → `instance.yaml` → `secret.sops.yaml` + `httproute.yaml`
- Wired via `kubernetes/apps/ai/paperclip/ks.yaml` (`targetNamespace: ai`, `prune: true`, `wait: true`, `interval: 1h`)
- Follows `kguardian` OCI pattern (`chartRef` → `OCIRepository`, Renovate-tracked `ref.tag`)
- Storage `ceph-block` (RWO → `Recreate` where applicable); `seLinuxRelabel: false`; ServiceMonitor enabled; `${SECRET_DOMAIN}` for URLs, no hardcoded domains

## 2. Components

- **Namespace:** existing `ai` (`kubernetes/apps/ai/paperclip/`, shares ns with litellm/llmkube/memini; simplifies LiteLLM access, matches shared-ns precedent like `security/kguardian`)
- **OCIRepository `paperclip-operator`:** `oci://ghcr.io/paperclipinc/charts/paperclip-operator`, tag `0.19.1`, `interval: 1h`
- **HelmRelease `paperclip-operator`:** `chartRef` → OCIRepository, `crds.install: true`, `crds.keep: true`, replicaCount 1, metrics enabled + `serviceMonitor.enabled: true`, `networkPolicy.enabled: false` (Cilium compat), default resources (100m/128Mi req, 500m/256Mi lim)
- **Instance `my-paperclip` (ns `ai`):** image `ghcr.io/paperclipai/paperclip` with tag pinned at plan time to the operator chart's `appVersion` (or latest stable GitHub release; never `latest`), `autoUpdate.enabled: false` in v1, `workload: StatefulSet`, `replicas: 1`, `database.mode: managed` (`storageSize: 10Gi`, `storageClass: ceph-block`), `storage.persistence` (`5Gi`, `ceph-block`, `/paperclip`), `deployment.mode: authenticated` + `exposure: private`, `auth.secretRef: paperclip-auth`, `adapters.apiKeysSecretRef: paperclip-api-keys`, `security.seLinuxRelabel: false`, `probes.type: auto`
- **Secrets (SOPS `secret.sops.yaml`):** `paperclip-auth` (`BETTER_AUTH_SECRET`), `paperclip-api-keys` (LLM keys via LiteLLM passthrough; exact `OPENAI_MODEL` value resolved at plan time from in-cluster LiteLLM, `OPENAI_BASE_URL=http://litellm.ai.svc.cluster.local/v1`, `OPENAI_API_KEY=${LITELLM_API_KEY}` substituteFrom pattern)
- **HTTPRoute:** internal only, `parentRef: envoy-internal`, hostname `paperclip.${SECRET_DOMAIN}`, backend ClusterIP `my-paperclip:3100`

## 3. Data flow

Browser → `paperclip.${SECRET_DOMAIN}` (envoy-internal Gateway) → HTTPRoute → ClusterIP `my-paperclip:3100` → StatefulSet pod (Node.js, `SERVE_UI=true`, `PAPERCLIP_BIND=0.0.0.0`).

Auth via Better Auth (`BETTER_AUTH_SECRET`); LLM calls via `paperclip-api-keys` → in-cluster LiteLLM (no direct egress to api.openai.com). Data on `/paperclip` PVC (5Gi ceph-block) + managed PostgreSQL 17 StatefulSet (10Gi ceph-block, auto-generated creds Secret). Metrics via operator `:8081` + instance ServiceMonitor → VictoriaMetrics. Config changes roll via config-hash annotation; Reloader restarts on SOPS rotation.

## 4. Error handling & ops

- Probes `auto` (TCP 3100 in `authenticated` mode, `/api/health` 403s without creds) + extended `startup.failureThreshold: 60` for DB init
- Instance phases (`Pending`/`Provisioning`/`Running`/`Degraded`/`Failed`) via `kubectl get pci`; conditions + Warning events (`MultiReplicaPreconditions`, `PDBMayBlockDrains`, `SchedulerGatingValid`)
- Single-replica v1: no HPA, PDB skipped (would block drains at min scale), `topologySpread` deferred to B
- Backups v1: app-native local only (PVC-backed); S3 CronJob + `restoreFrom` deferred to B; Flux `prune`/`wait`, PVCs retained
- NetworkPolicy: operator default-deny + Cilium — if pod-to-DB/DNS breaks, disable operator policy first (kguardian-class issue)

## 5. Validation

- `kustomize build ./kubernetes/apps/ai/paperclip/app` + `kubeconform`; Flux `${...}` escaping (`$$` for literals)
- `bash .agents/skills/pr-review/scripts/validate-pr.sh` (required)
- `helm template` operator `0.19.1` to confirm CRDs; `kubectl explain instance.spec` post-reconcile (ask-first, no live apply now)
- Flux diff in worktree before PR; post-merge `kubectl get pci`, `kubectl get pods -n ai -l app.kubernetes.io/instance=my-paperclip` (app + db-0), internal URL 200 via port-forward
- Renovate tracks `ref.tag`; image tag pinned

## Research basis

- Kubesearch `search_releases("paperclip")` = 0, `grep_values("paperclip")` = 0 — no homelab precedent (first adopter)
- Upstream `paperclipinc/paperclip-operator` verified pullable (`0.19.1`, digest `sha256:3b7a73…`); default values captured (`replicaCount: 1`, `:8081/healthz|readyz`, `leaderElection.enabled`, `crds.install/keep`)
- Cluster patterns reused: `security/kguardian/{ks,app/ocirepository,app/helmrelease,app/kustomization}.yaml`

## Deferred (Approach B)

External CNPG (`pgbouncer-rw.database.svc.cluster.local`), S3/R2 `objectStorage`, OAuth/Resend email, public HTTPRoute + TLS, HPA (`minReplicas`/`maxReplicas`), PDB, S3 backup CronJob with retention + `restoreFrom` clone, `autoUpdate` digests.
