# OpenCode SQLite metadata inspection (preparation only)

`../ks.yaml` registers `ai/opencode-inspection` **suspended**. Activation is a separately approved diff setting `suspend: false`; no direct apply or ephemeral containers.

## Contract

- Pod affinity follows the OpenCode pod onto its RWO PVC node; recheck one ready replica before activation and do not move it during the run.
- `/source` is `.local/share/opencode`, read-only at claim and mount. The script opens only `opencode.db`; SQLite handles WAL/SHM. Files must already be group-readable; never fix permissions live.
- The NetworkPolicy denies all traffic. Policies are additive, so recheck for overlapping allow policies before activation.
- Output is schema metadata, row counts, integrity and FK-violation totals. The fingerprint excludes defaults, CHECK/generated expressions, partial-index predicates and view/trigger bodies, so it is not proof of schema equivalence or a backup.

## Validate offline

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_inspect_db.py
```

Fixtures are synthetic; host tests do not prove read-only WAL access on the container mount.

## Run (after approval)

```sh
flux reconcile kustomization opencode-inspection -n ai --with-source
kubectl -n ai wait --for=condition=Complete job/opencode-inspection --timeout=330s
kubectl -n ai logs job/opencode-inspection -c inspect
```

The completed Job is kept and never reruns. Deleting it while it is still in Git recreates it; cleanup is a GitOps removal of this Kustomization.
