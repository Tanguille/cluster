# Temporary SQLite metadata inspection — preparation only

`opencode/ks.yaml` registers a **suspended** independent Flux Kustomization.
Production HelmRelease, replicas, configuration, routes and PVC are unchanged.
The preparation diff alone cannot start the Job. An exact activation diff changing
only this Kustomization's `suspend` to `false`, push, and reconciliation require
explicit approval. Do not use direct apply, ephemeral containers or live installs.

## Execution contract

- One Job, no retries, 300-second deadline, no TTL and no Flux force replacement.
- Required same-namespace pod affinity follows the existing OpenCode pod onto its
  RWO PVC node (observed `control-3`). Recheck one ready application replica before
  activation; do not relocate/stop it during inspection. Affinity is a scheduling
  condition, not a continuing placement lock.
- UID/GID 65532, no fsGroup or chown, no capabilities/token/privilege, read-only root.
  Existing database directory/files must be group-readable/traversable; failure is
  intentional if permissions differ. Never fix permissions live to force a result.
- Only `.local/share/opencode` is exposed at `/source`, read-only at both claim and
  mount level. This directory contains existing auth state as well as the DB;
  the script opens **only** `opencode.db` (SQLite manages its WAL/SHM). Directory
  exposure does not mean auth files are read. No home/global config mounts.
- Script ConfigMap only; no data output files, temporary storage, network or installs.
  NetworkPolicy selects only inspection pods and denies ingress/egress. Live Cilium
  `enable-policy=default` was verified on 2026-09-06. Recheck overlapping allow
  policies before activation: Kubernetes policies are additive.
- `mode=ro`, query-only, one read transaction; no immutable flag, checkpoint,
  OpenCode initialization, backup or copies. WAL/permission/SQLite errors fail closed
  with a fixed message, without SQL error text, partial reports or tracebacks.
- Logs contain schema names/column types/relationships/index structure, counts,
  aggregate integrity/FK status and a structural SHA-256. Never row IDs, values,
  defaults, DDL or JSON payloads. Unsupported identifier/type syntax and virtual
  tables fail closed. Views/triggers are named but never evaluated for counts.
- The structural fingerprint deliberately excludes defaults, CHECK expressions,
  generated expressions, partial-index predicates and view/trigger bodies. It is
  **not** proof of full schema equivalence or ALL-state preservation. This Job is
  inventory preparation, not a migration or recoverable backup.

## Image evidence

Read-only `docker buildx imagetools inspect python:3.13-alpine` on 2026-09-06:

- index: `sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a`
- amd64: `sha256:46ee549c88617e9bc8acb843a326f1a5c0fa5608d7f9703509efe6d53b55f318`
- upstream annotation: `3.13.15-alpine3.24`, source revision
  `688a0b86bb44289df16a363e9f41d90514c1a5f9` in docker-library/python.

## Offline validation

From this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_inspect_db.py
```

Fixtures are synthetic, outside the workspace/PVC; tests never take a live DB path.
Run repository `mise exec -- kustomize build` on this directory and the required
`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` from repo root.
Host fixture tests do not prove read-only WAL support of the eventual container
mount; that is an explicit live fail-closed gate, not an inferred success.

## Later approved execution / cleanup (not performed)

After approval of the exact activation diff and its push/merge, allow the existing
parent `flux-system/cluster-apps` reconciliation to discover/unsuspend
`ai/opencode-inspection`. The targeted command, only when approved, is:

```sh
flux reconcile kustomization opencode-inspection -n ai --with-source
kubectl -n ai wait --for=condition=Complete job/opencode-inspection --timeout=330s
kubectl -n ai logs job/opencode-inspection -c inspect
```

The parent name/path (`./kubernetes/apps`) was verified read-only on 2026-09-06.
Do not force a cluster-wide reconcile merely for convenience. Failure must
be investigated through metadata/fixed failure status, not retries with writable
mounts. A completed Job is retained: repeat reconciliation must not rerun it.
Deleting the Job while it remains desired in Git would recreate it. Cleanup needs
a separately approved GitOps removal/prune of this inspection Kustomization and
its Job, ConfigMap and NetworkPolicy; retain approved metadata evidence first.
