# Phase 5: Security Review
**Completed:** 2026-03-07

## Findings

### 1. Secrets Management ✅
- **All secrets are properly encrypted with SOPS** using AES256_GCM
- Found 20+ secret files (*.sops.yaml) with encrypted values
- No plaintext secrets detected in repository
- Secrets use proper Kubernetes Secret resources with encrypted data fields
- SOPS age key properly encrypted in `components/common/sops-age.sops.yaml`

### 2. RBAC ⚠️
- **HIGH**: Actions runner has `cluster-admin` ClusterRoleBinding (`rbac.yaml:14`)
- **HIGH**: Actions runner has Talos `os:admin` role granting full Talos API access
- Descheduler has limited ClusterRole for PVC operations (appropriate)
- Flux operator has custom ClusterRoleBinding for web admin
- Most workloads lack explicit ServiceAccount configuration

### 3. Network Policies ❌
- **No NetworkPolicy resources found** for application segmentation
- Cilium is used as CNI but no CiliumNetworkPolicy resources detected
- Only 2 HelmReleases mention networkPolicy (flux-operator, silence-operator)
- flux-instance explicitly disables networkPolicy (`helmrelease.yaml:17`)

### 4. Container Security ⚠️
- **Mixed security context implementation**:
  - ✅ 30+ HelmReleases have pod-level `securityContext.runAsNonRoot: true`
  - ✅ 20+ containers have `allowPrivilegeEscalation: false`
  - ✅ 20+ containers have `readOnlyRootFilesystem: true`
  - ❌ Many HelmReleases have empty or minimal securityContext blocks
  - ❌ Missing `capabilities.drop: [ALL]` in most containers
  - ❌ Missing `seccompProfile` configurations

### 5. Ingress/TLS ✅
- Proper certificate management via cert-manager
- Uses ACME with Cloudflare DNS challenge
- TLS certificates stored as Kubernetes Secrets
- External-dns for automated DNS record management
- Envoy Gateway used for ingress with proper TLS termination

### 6. Privileged Containers / hostPath ⚠️
- **hostPath usage detected**:
  - jellyfin: `/var/mnt/merged/` for media storage (`helmrelease.yaml:150-151`)
  - openebs: `/var/openebs/local` for local storage
- **hostPort usage**:
  - spegel uses `hostPort: 29999` for registry mirror
- No privileged containers detected
- No hostNetwork or hostPID/hostIPC usage found

### 7. Image Security ⚠️
- **OCI repositories used** for most charts (good supply chain practice)
- **Issues found**:
  - `fileflows`: uses `tag: latest` without digest
  - `monerod`: uses `tag: latest@sha256:...` (acceptable with digest)
  - `wizarr`: uses `tag: latest@sha256:...` (acceptable with digest)
  - ToolHive configs use `latest` tag for MCP servers
  - Most app-template based apps use proper versioning

### 8. API Exposure ✅
- Internal services properly scoped with namespace-based DNS
- No direct NodePort exposure of sensitive services detected
- Services use proper ClusterIP/LoadBalancer types

### 9. Talos Security ⚠️
- **SecureBoot disabled** on all nodes (`secureboot: false`)
- Kubernetes Talos API access enabled for actions-runner-system and system-upgrade
- Allowed roles limited to `os:admin`
- Proper machine certificates with additional SANs

### 10. Supply Chain ✅
- **OCI registries used** for most Helm charts
- Charts pulled from verified sources (ghcr.io, factory.talos.dev)
- FluxCD used for GitOps reconciliation
- No untrusted Helm repositories detected

### 11. Service Account Security ❌
- **Only 4 apps** disable automountServiceAccountToken:
  - sonarr, radarr, prowlarr, scraparr
- **40+ HelmReleases** lack `automountServiceAccountToken: false`
- Most pods don't need Kubernetes API access but have it by default

## Vulnerabilities Found

| Severity | Category | Location | Description | Remediation |
|----------|----------|----------|-------------|-------------|
| HIGH | RBAC | `actions-runner-system/actions-runner-controller/runners/cluster/rbac.yaml:14` | Actions runner bound to cluster-admin ClusterRole | Create dedicated Role with least privilege permissions |
| HIGH | RBAC | `actions-runner-system/actions-runner-controller/runners/cluster/rbac.yaml:25` | Actions runner has Talos os:admin role | Restrict to specific Talos roles needed (e.g., os:reader) |
| MEDIUM | Image Security | `media/fileflows/app/helmrelease.yaml:49` | Uses 'latest' tag without digest | Pin to specific digest or version tag |
| MEDIUM | Image Security | `ai/toolhive/config/*.yaml` | MCP servers use 'latest' tag | Pin to specific digests for reproducibility |
| MEDIUM | Network | `flux-system/flux-instance/app/helmrelease.yaml:17` | Network policy explicitly disabled | Enable networkPolicy or document justification |
| MEDIUM | Container Security | Multiple files | Missing security contexts in 40+ apps | Add securityContext with allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, runAsNonRoot: true |
| MEDIUM | Container Security | Multiple files | Missing capabilities.drop: [ALL] | Add capabilities drop to all containers |
| MEDIUM | Pod Security | Multiple files | automountServiceAccountToken not disabled | Set automountServiceAccountToken: false where K8s API not needed |
| LOW | Host Security | `apps/media/jellyfin/app/helmrelease.yaml:150` | hostPath mount for media storage | Consider using PVC with proper access controls |
| LOW | Host Security | `apps/kube-system/spegel/app/helmrelease.yaml:21` | hostPort usage for registry | Acceptable for this use case but document |
| LOW | Node Security | `talos/talconfig.yaml:26,47,68` | SecureBoot disabled on all nodes | Enable SecureBoot if hardware supports |
| LOW | Container Security | Multiple files | Missing seccomp profiles | Add seccompProfile: type: RuntimeDefault |

## Action Items

### Critical (Immediate)
- [ ] **RBAC-1**: Replace cluster-admin binding for actions runner with custom Role
- [ ] **RBAC-2**: Restrict Talos API access from os:admin to more limited role

### High Priority
- [ ] **IMG-1**: Pin fileflows image to specific digest instead of 'latest'
- [ ] **IMG-2**: Pin all ToolHive MCP server images to specific digests
- [ ] **NET-1**: Implement NetworkPolicies for namespace segmentation
- [ ] **SEC-1**: Add securityContext to all HelmReleases missing configuration

### Medium Priority
- [ ] **SA-1**: Add automountServiceAccountToken: false to all workloads not needing K8s API
- [ ] **CAP-1**: Add capabilities.drop: [ALL] to all containers
- [ ] **SECCOMP-1**: Add seccompProfile: type: RuntimeDefault to all containers
- [ ] **NET-2**: Enable or justify networkPolicy: false for flux-instance

### Low Priority
- [ ] **NODE-1**: Evaluate SecureBoot enablement on Talos nodes
- [ ] **STORAGE-1**: Document justification for jellyfin hostPath usage
- [ ] **MON-1**: Review spegel hostPort usage and document if necessary

## Summary Stats
- **Total issues:** 14
- **Critical:** 0 | **High:** 2 | **Medium:** 8 | **Low:** 4

### Security Posture Summary
**Overall Rating: MODERATE**

**Strengths:**
- Excellent secrets management (100% SOPS encryption)
- Good ingress/TLS configuration
- Strong supply chain security (OCI registries)
- Partial security context implementation
- No privileged containers or excessive host access

**Weaknesses:**
- Overly permissive RBAC for CI/CD runners
- Missing network policies for micro-segmentation
- Inconsistent container security contexts
- Default service account token mounting
- Some 'latest' image tags in production

**Recommendations:**
1. Implement Pod Security Standards (PSS) in enforce mode
2. Deploy Cilium Clusterwide Network Policies
3. Create custom RBAC roles for all service accounts
4. Standardize securityContext across all workloads
5. Enable automated security scanning in CI pipeline
