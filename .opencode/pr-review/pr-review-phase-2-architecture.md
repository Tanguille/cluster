# Phase 2 Architecture Review - PR #2223

## PR Summary
**Title:** feat(cloudnative-pg): update secrets and configurations for Ceph integration  
**Scope:** Ceph integration for CNPG backups across database/cloudnative-pg and rook-ceph namespaces

---

## Architecture Findings

### Critical Issues

| # | File | Issue | Severity | Details |
|---|------|-------|----------|---------|
| 1 | `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml` | **New files not referenced** | 🔴 Critical | `cephobjectstoreuser.yaml` and `rclone-sync.yaml` are not listed in `resources`. These files will not be applied by Flux. |
| 2 | `kubernetes/apps/database/cloudnative-pg/cluster/cephobjectstoreuser.yaml` | **Cross-namespace resource in wrong directory** | 🟡 Warning | CephObjectStoreUser has `namespace: rook-ceph` but is located in `database/cloudnative-pg/cluster/`. While functionally valid, this breaks organizational conventions. Consider relocating to `rook-ceph/rook-ceph/cluster/` or creating a dedicated location for cross-namespace resources. |

### Verification Checks

| Check | Status | Notes |
|-------|--------|-------|
| Directory structure follows `kubernetes/apps/<namespace>/<app>/` | ✅ Pass | All files in correct locations |
| SOPS secrets use `.sops.yaml` pattern | ✅ Pass | Both secrets properly encrypted with SOPS |
| Cross-namespace references use FQDN | ✅ Pass | `rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local:80` correctly used in objectstore.yaml and rclone-sync.yaml |
| CephObjectStoreUser references existing store | ✅ Pass | References `store: ceph-objectstore` which is defined in helmrelease.yaml |
| CronJob has proper resource limits | ✅ Pass | rclone-sync.yaml has requests/limits and `restartPolicy: OnFailure` |
| Kustomization references all resources | ❌ Fail | Missing `cephobjectstoreuser.yaml` and `rclone-sync.yaml` in cluster/kustomization.yaml |

### Resource Relationships

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         rook-ceph namespace                                  │
│  ┌──────────────────────────┐    ┌──────────────────────────────────────┐  │
│  │ cnpg-s3-secret (SOPS)    │    │ CephObjectStore (helmrelease.yaml)   │  │
│  │ - aws-access-key-id      │    │ - name: ceph-objectstore             │  │
│  │ - aws-secret-access-key  │    │ - RGW endpoint                       │  │
│  └───────────┬──────────────┘    └──────────────┬───────────────────────┘  │
│              │                                  │                          │
│              └──────────────────────────────────┘                          │
│                 (CephObjectStoreUser keys reference)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         database namespace                                   │
│  ┌──────────────────────────┐    ┌──────────────────────────────────────┐  │
│  │ cloudnative-pg-secret    │◄───┤ CephObjectStoreUser                  │  │
│  │ (updated credentials)    │    │ - namespace: rook-ceph               │  │
│  └───────────┬──────────────┘    │ - store: ceph-objectstore            │  │
│              │                   │ - keys reference cnpg-s3-secret      │  │
│              ▼                   └──────────────────────────────────────┘  │
│  ┌──────────────────────────┐                                              │
│  │ ObjectStore (barman)     │◄─────────────────────────────────────────┐  │
│  │ - endpointURL: RGW FQDN  │                                          │  │
│  │ - destinationPath: s3:// │                                          │  │
│  │ - retentionPolicy: 30d   │                                          │  │
│  └───────────┬──────────────┘                                          │  │
│              │                                                         │  │
│              ▼                                                         │  │
│  ┌──────────────────────────┐                                          │  │
│  │ Cluster (CNPG)           │                                          │  │
│  │ - barmanObjectName       │                                          │  │
│  └──────────────────────────┘                                          │  │
│                                                                        │  │
│  ┌──────────────────────────┐                                          │  │
│  │ CronJob (rclone-sync)    │──────────────────────────────────────────┘  │
│  │ - Syncs Ceph → TrueNAS   │    (reads from ceph:cnpg-backups)          │
│  │ - Runs every 6 hours     │                                             │
│  └──────────────────────────┘                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### File-Level Details

| File | Changes | Architecture Assessment |
|------|---------|------------------------|
| `secret.sops.yaml` | Updated encrypted values | ✅ SOPS format correct, annotations preserved |
| `cephobjectstoreuser.yaml` | **NEW** - CephObjectStoreUser for CNPG backup access | ⚠️ **NOT in kustomization.yaml** - will not be applied |
| `cluster.yaml` | Changed `barmanObjectName` from `r2` to `ceph-objectstore` | ✅ References correct ObjectStore name |
| `objectstore.yaml` | Renamed to `ceph-objectstore`, updated endpoint, retention 14d→30d | ✅ Uses correct FQDN for RGW endpoint |
| `rclone-sync.yaml` | **NEW** - CronJob for backup sync to TrueNAS | ⚠️ **NOT in kustomization.yaml** - will not be applied; has proper resources and restartPolicy |
| `cnpg-s3-secret.sops.yaml` | **NEW** - S3 credentials for CephObjectStoreUser | ✅ Listed in kustomization.yaml, properly encrypted |
| `kustomization.yaml` (rook-ceph) | Added cnpg-s3-secret.sops.yaml reference | ✅ Properly updated |
| `helmrelease.yaml` | Added cephObjectStores config for ceph-objectstore | ✅ RGW configured with erasure coding, proper resources |

### Recommendations

1. **Add missing references to kustomization.yaml**:
   ```yaml
   # kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml
   resources:
     - cephobjectstoreuser.yaml  # ADD THIS
     - cluster.yaml
     - objectstore.yaml
     - pooler.yaml
     - pooler-ro.yaml
     - prometheusrule.yaml
     - rclone-sync.yaml  # ADD THIS
     - scheduledbackup.yaml
   ```

2. **Consider moving CephObjectStoreUser** (optional):
   - Since the resource is in `rook-ceph` namespace, consider relocating the file to `kubernetes/apps/rook-ceph/rook-ceph/cluster/` for better organization
   - Update the rook-ceph cluster kustomization.yaml accordingly

3. **Verify TrueNAS NFS availability**:
   - The rclone-sync CronJob depends on NFS mount to `${TRUENAS_IP}`
   - Ensure this endpoint is stable and accessible from the database namespace

---

## Summary

| Category | Count |
|----------|-------|
| Critical Issues | 1 |
| Warnings | 1 |
| Pass | 6 |

**Overall Status**: ❌ **BLOCKED** - Cannot merge until kustomization.yaml is updated to include the new resources.
