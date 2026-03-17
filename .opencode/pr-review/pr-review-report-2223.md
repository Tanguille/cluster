# PR Review Report: #2223

**PR:** feat(cloudnative-pg): update secrets and configurations for Ceph integration  
**Branch:** `feat(cnpg)/backup-to-ceph-primarily` → `main`  
**Status:** Draft, Clean mergeable state  
**Files Changed:** 8 (+153, -18)

---

## Executive Summary

| Phase | Status | Critical Issues |
|-------|--------|-----------------|
| 1. YAML Syntax | ✅ PASS | 0 |
| 2. Architecture | 🔴 **BLOCKED** | 1 |
| 3. Best Practices | ✅ PASS | 0 |
| 4. Security | ✅ PASS | 0 |
| 5. Deduplication | ✅ PASS | 0 |
| 6. Validation | ✅ PASS | 0 |

**Overall:** 🔴 **BLOCKED** - Cannot merge until 1 critical issue is resolved.

---

## 🔴 Critical Issue (Blocking)

### Missing Kustomization References

**Problem:** Two new files are not referenced in `kustomization.yaml`:
- `kubernetes/apps/database/cloudnative-pg/cluster/cephobjectstoreuser.yaml`
- `kubernetes/apps/database/cloudnative-pg/cluster/rclone-sync.yaml`

**Impact:** These resources will **NOT** be applied by Flux even after merge.

**Fix:** Add to `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml`:

```yaml
resources:
  - cephobjectstoreuser.yaml  # ADD THIS
  - cluster.yaml
  - objectstore.yaml
  - rclone-sync.yaml          # ADD THIS
```

---

## ⚠️ Warnings (Non-blocking)

### 1. Namespace Mismatch
**File:** `cephobjectstoreuser.yaml`  
**Issue:** File is in `database/cloudnative-pg/cluster/` but resource has `namespace: rook-ceph`  
**Recommendation:** Consider moving to `rook-ceph/rook-ceph/app/` for consistency, or document why cross-location is needed.

### 2. CronJob History Limits
**File:** `rclone-sync.yaml`  
**Current:** `successfulJobsHistoryLimit: 3`  
**Recommendation:** Consider `1` to reduce etcd pressure since job logs aren't typically needed long-term for sync jobs.

### 3. Document Start Markers
**Files:** `cephobjectstoreuser.yaml`, `cnpg-s3-secret.sops.yaml`  
**Issue:** Missing `---` document start marker  
**Recommendation:** Add for consistency with other files.

---

## ✅ What Looks Good

### Security
- All secrets properly SOPS encrypted with age
- No plaintext credentials
- Proper secretKeyRef usage throughout
- Resource limits defined on CronJob
- Internal service endpoints use FQDN

### Best Practices
- Excellent DRY usage with YAML anchors (`&plugin`, `&barmanParameters`)
- Proper use of `${SECRET_DOMAIN}` and `${TRUENAS_IP}` templates
- Consistent naming (lowercase-dashes)
- SOPS encrypted files follow conventions

### Architecture
- Proper cross-namespace references using FQDN (`rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local`)
- CephObjectStoreUser quotas are reasonable (10 buckets, 500Gi)
- HelmRelease update correctly adds cephObjectStores
- Retention policy increased to 30d (good data protection)

### Validation
- All kustomize builds succeed
- All CRD/API versions valid
- YAML syntax valid throughout
- All cross-references resolve correctly

---

## Per-Phase Summaries

### Phase 1: YAML Syntax ✅
- All 8 files pass validation
- 2-space indentation correct
- LF line endings
- Proper SOPS encryption
- No trailing whitespace

### Phase 2: Architecture 🔴
- **BLOCKING:** 2 files not in kustomization.yaml
- Cross-namespace FQDN usage correct
- Proper directory structure otherwise

### Phase 3: Best Practices ✅
- No convention violations
- Good DRY patterns
- Proper templating

### Phase 4: Security ✅
- All security checks pass
- 2 minor info-level items
- No vulnerabilities

### Phase 5: Deduplication ✅
- First CronJob in repo (establishes pattern)
- No duplicate functionality
- Consistent with existing patterns

### Phase 6: Validation ✅
- All kustomize builds succeed
- All schemas valid
- All references resolve

---

## Action Items

**Before Merge:**
- [ ] **CRITICAL:** Add `cephobjectstoreuser.yaml` and `rclone-sync.yaml` to `cluster/kustomization.yaml`
- [ ] Optionally add `---` document start markers to new files
- [ ] Consider reducing `successfulJobsHistoryLimit` to 1

**Optional Improvements:**
- [ ] Add yaml-language-server schema comment to `cnpg-s3-secret.sops.yaml`
- [ ] Document why CephObjectStoreUser is in cloudnative-pg directory vs rook-ceph
- [ ] Add README documenting the backup flow (Ceph → TrueNAS)

---

## Verdict

**Status:** 🔴 **CHANGES REQUESTED**

The PR is well-structured and follows conventions, but **cannot be merged** until the kustomization.yaml is updated to include the new resources. This is a common oversight when adding new files - they exist in the repo but won't be applied by Flux.

Once the kustomization references are added, this PR is ready for merge.
