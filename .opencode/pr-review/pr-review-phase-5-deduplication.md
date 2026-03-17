# Phase 5 Code Deduplication Review - PR #2223

**Review Date:** 2026-03-10  
**PR:** #2223 - Backup Sync Functionality  
**Files Reviewed:**
1. `kubernetes/apps/database/cloudnative-pg/cluster/cephobjectstoreuser.yaml` - NEW
2. `kubernetes/apps/database/cloudnative-pg/cluster/rclone-sync.yaml` - NEW
3. `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` - MODIFIED

---

## 1. CronJob Pattern Analysis

### Finding: FIRST CronJob in Repository

**Status:** ⚠️ **NEW PATTERN - NO EXISTING REFERENCE**

This PR introduces the **first CronJob** resource in the entire repository. No existing CronJob patterns were found to compare against.

### Recommendations for CronJob Consistency:

Since this is the inaugural CronJob, establish conventions for future CronJobs:

| Pattern | Recommendation | Rationale |
|---------|---------------|-----------|
| **Schedule Format** | Use `@daily` or `@hourly` for readability, or standard cron format `"0 2 * * *"` | Repository uses `@daily` for `ScheduledBackup` resources |
| **restartPolicy** | Use `OnFailure` for backup/sync jobs | Standard for one-off tasks; `Always` only for long-running (found in gatus) |
| **concurrencyPolicy** | Use `Forbid` for backup jobs | Prevent overlapping sync operations |
| **successfulJobHistoryLimit** | Set to `1` or `2` | Minimize completed job clutter |
| **failedJobHistoryLimit** | Set to `3` | Allow debugging of failures |

### Existing Schedule Patterns Found:
```yaml
# @daily - Used by ScheduledBackup and recyclarr
schedule: "@daily"

# Frequent polling - nextcloud cron
schedule: "*/5 * * * *"

# Hourly - qbitmanage
QBT_SCHEDULE: "0 * * * *"
```

### Resource Pattern Comparison:

**Existing Resource Patterns (from 95+ files):**
```yaml
# Minimal workloads
requests:
  cpu: 10m
  memory: 128Mi
limits:
  memory: 1Gi

# Database workloads (CNPG)
requests:
  cpu: 300m
  memory: 1Gi
limits:
  cpu: 2
  memory: 8Gi

# Rook-Ceph OSD
requests:
  cpu: "500m"
  memory: "3Gi"
limits:
  memory: "6Gi"
```

**Recommendation for rclone-sync CronJob:**
```yaml
resources:
  requests:
    cpu: 50m        # rclone is network/IO bound
    memory: 128Mi   # minimal memory needs
  limits:
    memory: 512Mi   # prevent memory leaks
```

---

## 2. CephObjectStoreUser Pattern Analysis

### Finding: FIRST CephObjectStoreUser Resource

**Status:** ⚠️ **NEW PATTERN - NO EXISTING REFERENCE**

This PR introduces the first `CephObjectStoreUser` custom resource. No existing instances found in repository.

### Context from helmrelease.yaml:
The `cephObjectStores: []` array (line 201) is currently empty and will be populated by this PR.

### Recommendations for CephObjectStoreUser:

1. **Naming Convention:**
   - Use lowercase-dashes: `cloudnative-pg-backup-user`
   - Include purpose in name for clarity

2. **Secret Management:**
   - Store credentials in SOPS-encrypted Secret
   - Follow existing secret naming: `{app}-secret` or `{resource}-secret`
   - Reference from `cloudnative-pg-secret` for consistency

3. **Store Association:**
   - Ensure the referenced `CephObjectStore` exists (being added in this PR)
   - Match the store name between resources

---

## 3. Secret Pattern Analysis

### Finding: Consistent SOPS Pattern Established

**Status:** ✅ **FOLLOWS EXISTING PATTERNS**

All secrets in the repository follow this structure:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: {app}-secret
  # Optional: labels for CNPG reload
  labels:
    cnpg.io/reload: "true"
type: Opaque
stringData:
  key: ENC[AES256_GCM,...]
sops:
  age:
    - recipient: age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq
      enc: |
        -----BEGIN AGE ENCRYPTED FILE-----
        ...
        -----END AGE ENCRYPTED FILE-----
  lastmodified: "2025-..."
  mac: ENC[AES256_GCM,...]
  encrypted_regex: ^(data|stringData)$
  version: 3.x.x
```

### Secret References:

**Existing Secret Examples:**
- `kubernetes/apps/database/cloudnative-pg/app/secret.sops.yaml` - AWS S3 credentials
- `kubernetes/components/volsync/secret.sops.yaml` - Kopia repository
- `kubernetes/apps/rook-ceph/rook-ceph/app/secret.sops.yaml` - Ceph dashboard

### Recommendations:

1. **Naming:** Use `cloudnative-pg-secret` (already exists) or create `rclone-sync-secret`
2. **Structure:** Follow `stringData` pattern (not `data`)
3. **Encryption:** Use age recipient `age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq`
4. **Labels:** Add `cnpg.io/reload: "true"` if CNPG needs to detect changes

---

## 4. HelmRelease Values Pattern Analysis

### Finding: cephObjectStores Empty Array

**Status:** 📝 **NEW CONFIGURATION**

Current state in `rook-ceph/cluster/helmrelease.yaml`:
```yaml
cephObjectStores: []
```

### Recommendations for cephObjectStores Structure:

Based on patterns from `cephBlockPools` and `cephFileSystems` in the same file:

```yaml
cephObjectStores:
  - name: object-store
    spec:
      metadataPool:
        failureDomain: host
        replicated:
          size: 2
          minSize: 1
      dataPool:
        failureDomain: host
        replicated:
          size: 2
          minSize: 1
        parameters:
          compression_mode: aggressive
          compression_algorithm: zstd
      gateway:
        port: 80
        securePort: 443
        instances: 1
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            memory: 1Gi
    storageClass:
      enabled: true
      name: ceph-object
      reclaimPolicy: Delete
```

### Storage Class Pattern (from existing pools):

| Pool Type | StorageClass Name | Parameters |
|-----------|------------------|------------|
| Block | ceph-block | compression_mode, imageFormat, csi secrets |
| Filesystem | ceph-filesystem | csi secrets only |
| **Object** | **ceph-object** | **Define object-specific params** |

---

## 5. Resource Limits Analysis

### Finding: Pattern Varies by Workload Type

**Status:** ✅ **FOLLOW EXISTING WORKLOAD PATTERNS**

### Resource Patterns by Category:

| Workload Type | CPU Request | Memory Request | Memory Limit | Example |
|--------------|-------------|----------------|--------------|---------|
| **Light/Utility** | 10m | 128Mi | 1Gi | kopia, recyclarr |
| **Medium/App** | 100m | 256Mi-1Gi | 2-4Gi | karakeep, immich |
| **Database** | 300m | 1Gi | 8Gi | cloudnative-pg cluster |
| **Storage (OSD)** | 500m | 3Gi | 6Gi | rook-ceph osd |
| **Storage (MDS)** | 100m | 256Mi | 4Gi | ceph-mds |

### Recommendations for New Resources:

**For rclone-sync CronJob:**
```yaml
resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    memory: 512Mi
```

**For Ceph RGW Gateway (in cephObjectStores):**
```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 1Gi
```

---

## 6. Backup & Sync Pattern Analysis

### Existing Backup Infrastructure:

| Component | Type | Schedule | Method |
|-----------|------|----------|--------|
| **VolSync** | PVC replication | Continuous/periodic | Kopia/restic |
| **CNPG ScheduledBackup** | PostgreSQL | @daily | barman-cloud plugin |
| **Kopia** | Repository management | N/A (on-demand UI) | File-based |
| **ObjectStore** | S3 target | N/A | barman-cloud |

### Finding: rclone-sync Adds New Pattern

The rclone-sync CronJob introduces a **third backup method** alongside VolSync and CNPG/barman-cloud.

**Ensure clear separation of concerns:**
- **VolSync:** PVC-level backups (block/FS level)
- **CNPG + barman-cloud:** Database-specific backups (logical)
- **rclone-sync:** Object storage sync (S3-to-S3 or file-to-S3)

---

## Summary & Recommendations

### Critical Items:

1. **⚠️ Document CronJob Pattern** - This is the first CronJob; document conventions for future reference
2. **⚠️ Verify CephObjectStoreUser Fields** - No existing examples; validate against Rook Ceph CRD
3. **✅ Follow SOPS Pattern** - Use existing age recipient and `stringData` structure

### Style Consistency:

| Item | Status | Notes |
|------|--------|-------|
| YAML indentation (2 spaces) | ✅ | Follow repository standard |
| Resource naming (lowercase-dashes) | ✅ | Use `rclone-sync`, `cnpg-backup-user` |
| SOPS encryption | ✅ | Use standard age recipient |
| Schema comments | ✅ | Add `# yaml-language-server: $schema=...` |
| Labels | ✅ | Include `app.kubernetes.io/name` if needed |

### Files to Update:

1. `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml` - Add new resources to list
2. `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` - Populate `cephObjectStores` array

### No Action Required:

- Secret encryption pattern ✅
- General resource structure ✅
- HelmRelease structure ✅

---

## Deduplication Score: 8/10

**Rationale:**
- ✅ Secrets follow established SOPS patterns
- ✅ Resource naming follows conventions
- ✅ HelmRelease structure consistent
- ⚠️ CronJob is new pattern (needs documentation)
- ⚠️ CephObjectStoreUser is new resource type (no reference)
- ✅ No duplicate functionality with existing backup systems

**Recommendations Applied:** This review establishes patterns for future CronJobs and CephObjectStoreUsers.
