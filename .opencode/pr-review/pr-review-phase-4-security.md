# Phase 4 Security Review - PR #2223

**PR:** [#2223 - feat(cloudnative-pg): update secrets and configurations for Ceph integration](https://github.com/Tanguille/cluster/pull/2223)  
**Review Date:** 2026-03-10  
**Status:** DRAFT  
**Files Reviewed:** 8 files (153 additions, 18 deletions)

---

## Executive Summary

This PR introduces Ceph S3 integration for CloudNativePG backups, including:
- 2 new SOPS encrypted secrets
- 1 new CephObjectStoreUser resource
- 1 new CronJob for backup synchronization
- Configuration changes to existing Ceph and CNPG resources

**Overall Security Posture:** GOOD with minor concerns

---

## Security Findings Summary Table

| ID | Severity | Category | File | Status | Description |
|----|----------|----------|------|--------|-------------|
| SEC-001 | INFO | SOPS | secret.sops.yaml | PASS | Properly encrypted with age, encrypted_regex configured |
| SEC-002 | INFO | SOPS | cnpg-s3-secret.sops.yaml | PASS | Properly encrypted with age, encrypted_regex configured |
| SEC-003 | LOW | Secret Management | cephobjectstoreuser.yaml | WARN | Cross-namespace secret reference (CNPG → Rook-Ceph) |
| SEC-004 | INFO | Ceph Security | cephobjectstoreuser.yaml | PASS | Quotas configured: maxBuckets=10, maxSize=500Gi |
| SEC-005 | MEDIUM | Network Security | objectstore.yaml | WARN | HTTP endpoint without TLS (internal service) |
| SEC-006 | INFO | Data Retention | objectstore.yaml | PASS | Retention policy extended 7d → 30d |
| SEC-007 | LOW | CronJob Security | rclone-sync.yaml | WARN | External NFS dependency (TrueNAS) |
| SEC-008 | INFO | Resource Limits | rclone-sync.yaml | PASS | CPU/Memory limits defined |
| SEC-009 | INFO | Secret Mounting | rclone-sync.yaml | PASS | Uses secretKeyRef for credentials |
| SEC-010 | INFO | Ceph Config | helmrelease.yaml | PASS | Erasure coding with 2:1 ratio |

---

## Detailed Findings

### SEC-001 & SEC-002: SOPS Encryption Verification ✅

**Files:**
- `kubernetes/apps/database/cloudnative-pg/app/secret.sops.yaml`
- `kubernetes/apps/rook-ceph/rook-ceph/app/cnpg-s3-secret.sops.yaml`

**Verification:**
| Check | Status | Details |
|-------|--------|---------|
| `sops:` section present | ✅ PASS | Both files have sops metadata |
| `encrypted_regex: ^(data|stringData)$` | ✅ PASS | Correctly configured |
| Age recipient key | ✅ PASS | `age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq` |
| No plaintext secrets | ✅ PASS | All values encrypted with AES256_GCM |
| MAC verification | ✅ PASS | SOPS MAC present |

**Recommendation:** No action required.

---

### SEC-003: Cross-Namespace Secret Reference ⚠️ LOW

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/cephobjectstoreuser.yaml`

**Issue:** The CephObjectStoreUser resource is created in the `database` namespace but references a secret (`cnpg-s3-secret`) that exists in the `rook-ceph` namespace.

```yaml
apiVersion: ceph.rook.io/v1
kind: CephObjectStoreUser
metadata:
  name: cnpg-backup
  namespace: rook-ceph  # Secret is in rook-ceph
spec:
  keys:
    - accessKeyRef:
        name: cnpg-s3-secret  # In rook-ceph namespace
```

**Risk:** Rook Ceph operator must have permissions to read secrets across namespaces. This is a valid pattern for CephObjectStoreUser but should be documented.

**Recommendation:** 
- Ensure the CephObjectStoreUser CRD is designed for cross-namespace secret references
- Consider adding a comment documenting this intentional cross-namespace reference

---

### SEC-004: Ceph Quota Configuration ✅

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/cephobjectstoreuser.yaml`

**Configuration:**
```yaml
quotas:
  maxBuckets: 10
  maxSize: "500Gi"
```

**Assessment:**
- ✅ maxBuckets: 10 is reasonable for backup operations
- ✅ maxSize: 500Gi provides sufficient headroom for database backups
- ✅ Prevents accidental resource exhaustion

**Recommendation:** No action required.

---

### SEC-005: HTTP Endpoint Without TLS ⚠️ MEDIUM

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/objectstore.yaml`

**Issue:**
```yaml
endpointURL: http://rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local:80
```

**Analysis:**
- The endpoint uses HTTP (not HTTPS)
- However, this is an internal cluster service (`.svc.cluster.local`)
- Traffic never leaves the cluster network
- Acceptable for internal service-to-service communication

**Risk:** LOW - Internal cluster traffic only

**Recommendation:** 
- Document that this is intentional for internal cluster communication
- Consider adding a comment: `# Internal cluster endpoint - TLS termination at cluster boundary`

---

### SEC-006: Data Retention Policy Change ✅

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/objectstore.yaml`

**Change:**
```diff
-  retentionPolicy: 7d
+  retentionPolicy: 30d
```

**Assessment:**
- Increases backup retention from 7 to 30 days
- Storage impact: ~4x increase in storage requirements
- Improves disaster recovery capabilities
- Ceph quotas (500Gi) should accommodate this

**Recommendation:** Monitor storage usage to ensure 500Gi quota is sufficient.

---

### SEC-007: External NFS Dependency ⚠️ LOW

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/rclone-sync.yaml`

**Issue:**
```yaml
volumes:
  - name: truenas
    nfs:
      server: "${TRUENAS_IP}"
      path: /mnt/TanguilleServer/cnpg-backups
```

**Assessment:**
- CronJob syncs backups to external TrueNAS system
- Introduces external dependency for backup strategy
- If TrueNAS is unavailable, sync jobs will fail
- NFS traffic is unencrypted (standard for NFSv3/v4)

**Risk:** LOW - Backup sync will fail but primary Ceph backups remain intact

**Recommendation:**
- Ensure TrueNAS is on trusted network
- Consider adding monitoring/alerting for failed sync jobs
- Document TrueNAS as part of backup infrastructure

---

### SEC-008 & SEC-009: CronJob Resource & Secret Configuration ✅

**File:** `kubernetes/apps/database/cloudnative-pg/cluster/rclone-sync.yaml`

**Configuration:**
```yaml
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 512Mi
env:
  - name: RCLONE_CONFIG_CEPH_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: cloudnative-pg-secret
        key: aws-access-key-id
```

**Assessment:**
- ✅ Resource limits defined (prevents resource exhaustion)
- ✅ Secrets mounted via environment variables with secretKeyRef
- ✅ No privileged mode
- ✅ Uses official rclone image (rclone/rclone:1.68)

**Recommendation:** No action required.

---

### SEC-010: Ceph Erasure Coding Configuration ✅

**File:** `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`

**Configuration:**
```yaml
erasureCoded:
  dataChunks: 2
  codingChunks: 1
```

**Assessment:**
- 2:1 erasure coding ratio (2 data + 1 parity)
- 50% storage overhead
- Can tolerate 1 OSD failure
- Good balance of durability vs storage efficiency

**Recommendation:** No action required.

---

## Hardcoded Values Audit

| Value | File | Context | Assessment |
|-------|------|---------|------------|
| `rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local:80` | objectstore.yaml | Internal S3 endpoint | ✅ Internal FQDN, acceptable |
| `${TRUENAS_IP}` | rclone-sync.yaml | NFS server | ✅ Uses variable, not hardcoded |
| `${SECRET_R2_HOST}` | objectstore.yaml | (removed) | ✅ No longer referenced |

**Result:** No hardcoded credentials or external endpoints found.

---

## Compliance Checklist

| Requirement | Status | Notes |
|-------------|--------|-------|
| All secrets SOPS encrypted | ✅ | Both secrets properly encrypted |
| No plaintext credentials | ✅ | No credentials in non-secret files |
| Resource limits defined | ✅ | CronJob has limits |
| No privileged containers | ✅ | No privileged mode specified |
| Internal service references | ✅ | Uses proper FQDNs |
| Ceph quotas configured | ✅ | maxBuckets and maxSize set |
| Cross-namespace references documented | ⚠️ | Should add comment |

---

## Action Items

### Required (Before Merge)
_None - all security requirements met._

### Recommended (Post-Merge)
1. **SEC-003:** Add comment to `cephobjectstoreuser.yaml` documenting cross-namespace secret reference
2. **SEC-005:** Add comment to `objectstore.yaml` explaining HTTP for internal communication
3. **Monitor:** Track Ceph storage usage to ensure 500Gi quota is sufficient for 30-day retention
4. **Alerting:** Add alert for failed rclone-sync CronJob runs

---

## Conclusion

**Security Approval:** ✅ APPROVED with recommendations

The PR meets security requirements:
- All secrets properly encrypted with SOPS
- No hardcoded credentials
- Resource limits defined
- Ceph quotas prevent resource exhaustion
- Internal endpoints use cluster-local FQDNs

**Minor concerns:**
- Cross-namespace secret reference (acceptable for CephObjectStoreUser pattern)
- HTTP internal communication (acceptable for cluster-internal traffic)
- External NFS dependency for sync (acceptable with monitoring)

**Next Steps:**
1. Address optional recommendations
2. Proceed with merge
3. Monitor storage usage after deployment

---

*Review completed by Claude Code (opencode) on 2026-03-10*
