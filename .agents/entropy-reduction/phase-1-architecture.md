# Phase 1: Architecture Review
**Completed:** 2026-03-07

## Findings

### 1. Folder Structure
**Status:** ✅ Matches AGENTS.md specification

The repository follows the documented structure:
- `kubernetes/` - Contains all manifests, HelmReleases, and Kustomizations
- `talos/` - Machine configs with clusterconfig/, patches/, truenas-exporters/
- `docs/` - Documentation files (useful_commands.md, troubleshooting guides)
- `.agent/` - On-demand context files

### 2. Layer Separation
**Status:** ✅ Properly Structured

HelmReleases are correctly structured with Kustomizations:
- 92 `helmrelease.yaml` files found
- 81 `ks.yaml` files (Kustomization manifests)
- Each app has its own ks.yaml that references the app directory
- Parent Kustomization at `kubernetes/flux/cluster/ks.yaml` with patches for HelmRelease defaults

### 3. kubernetes/ Organization
**Status:** ✅ Well Organized

**Structure:**
- `apps/` - 15 category directories containing 62 applications
- `components/` - Reusable Kustomize components (volsync, nfs-scaler, common/repos)
- `flux/` - Bootstrap and cluster-level configuration

**Categories:**
- actions-runner-system, ai, cert-manager, database, default, flux-system
- kube-system, media, network, observability, openebs-system
- rook-ceph, security, system-upgrade, volsync-system, web3

### 4. Key Dependencies

**Infrastructure Dependencies:**
- `volsync-system/volsync` → Used by: nextcloud, jellyfin, immich, karakeep, sonarr, radarr, bazarr, prowlarr
- `database/cloudnative-pg-cluster` → Used by: nextcloud, crowdsec
- `database/dragonfly-cluster` → Used by: nextcloud
- `cert-manager` → Used by: envoy-gateway, cloudnative-pg-barman-cloud
- `openebs-system/openebs` → Used by: cloudnative-pg-cluster

**Application Dependencies:**
- Media stack: qbittorrent → radarr, sonarr, bazarr, prowlarr, cross-seed
- Nextcloud → notify-push, facerecognition
- Crowdsec → crowdsec-bouncers
- Cloudnative-pg → barman-cloud → cluster

### 5. Circular Dependencies
**Status:** ✅ None Found

Dependency graph analysis shows clean hierarchical dependencies with no circular references.

### 6. Large YAML Files (>300 lines)

| Lines | File | Notes |
|-------|------|-------|
| 455 | `apps/default/nextcloud/app/helmrelease.yaml` | Complex multi-container app |
| 270 | `apps/media/recyclarr/app/includes/Sonarr_Standard_Custom_Formats.yaml` | Configuration includes |
| 235 | `apps/ai/toolhive/config/homeassistant.yaml` | MCP server configuration |
| 223 | `apps/media/qbittorrent/tools/qbitmanage/config/config.yaml` | Tool configuration |
| 221 | `apps/network/envoy-gateway/app/envoy.yaml` | Gateway configuration |
| 201 | `apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` | Ceph cluster config |

### 7. Separation of Concerns
**Status:** ✅ Good

- **Apps** (`apps/*/`) - Application workloads with clear namespace boundaries
- **Infrastructure** (`apps/kube-system/`, `apps/rook-ceph/`, `apps/openebs-system/`) - Cluster infrastructure
- **Flux Config** (`flux/cluster/`, `flux/bootstrap/`) - GitOps reconciliation setup
- **Components** (`components/`) - Reusable Kustomize components for volsync, nfs-scaler

### 8. Naming Patterns
**Status:** ✅ Consistent

**Positive:**
- All files use `.yaml` extension (no `.yml` mixing)
- Consistent `ks.yaml` naming for Kustomizations
- Lowercase-dashes convention followed
- App directories match namespace names

**Observations:**
- 80 ks.yaml files, all with namespace declarations
- 412 total namespace declarations across codebase
- Consistent use of YAML anchors (`&namespace`, `*namespace`) for namespace references

## Issues Found

| Severity | Location | Issue | Recommendation |
|----------|----------|-------|----------------|
| Medium | `apps/network/external-service/ks.yaml` | 153 lines with 9 nearly identical Kustomization declarations | Consider using Kustomize components or generators to reduce repetition |
| Low | `apps/default/nextcloud/app/helmrelease.yaml` | 455 lines - very large configuration | Consider splitting into multiple HelmReleases or using valuesFrom |
| Low | Multiple apps | Large include files (recyclarr, toolhive) | Consider moving large configs to ConfigMaps or external files |
| Low | Various ks.yaml | Some missing `wait: true` | Consider standardizing wait behavior for infrastructure components |

## Action Items

- [ ] Refactor external-service ks.yaml to reduce repetition (extract common patterns to component)
- [ ] Review nextcloud helmrelease for potential splitting into smaller components
- [ ] Audit wait: behavior consistency across infrastructure components
- [ ] Document dependency graph for complex stacks (media, nextcloud)
- [ ] Consider extracting large config files to ConfigMaps with external data sources
- [ ] Clean up archive/ directory (old migration scripts from 2024-2025)
- [ ] Review .private/ directory for cleanup (timestamped backup folders)
- [ ] Investigate bootstrap/helmfile.d/ directory - appears to be unused legacy bootstrap
- [ ] Check empty directories: media/exportarr-dashboard, media/ipmi-exporter (observability/exporters)

## Additional Observations

### Legacy Artifacts
- `archive/` contains migration scripts from 2024-2025 that may no longer be needed
- `.private/` contains timestamped backup folders from 2024-2025
- `bootstrap/helmfile.d/` appears unused - cluster uses FluxCD not helmfile

### Potential Organization Issues
- `kubernetes/apps/media/` contains application apps (radarr, sonarr, etc.)
- `kubernetes/apps/observability/exporters/` contains media-related exporters (radarr-exporter, sonarr-exporter, etc.)
- This separation is functional but could cause confusion about where media-related items belong

### Minor Inconsistencies
- Empty directories found: `media/exportarr-dashboard`, `media/ipmi-exporter` in observability
- Some apps have inconsistent subdirectory patterns (e.g., some use /app/, others use root)

## Summary Stats

- **Total issues:** 8
- **Critical:** 0 | **High:** 0 | **Medium:** 1 | **Low:** 7

**Repository Health:** Good

**Key Metrics:**
- 62+ applications across 15 categories
- 93 HelmReleases
- 80+ Kustomizations (ks.yaml files)
- 100+ Kustomize resources
- 40+ encrypted secrets (SOPS)
- 0 circular dependencies
- 100% YAML extension consistency

**Additional Counts (from this review):**
- Legacy directories: archive/, .private/, bootstrap/helmfile.d
- Empty/incomplete directories: 2 found
- Total lines in YAML: ~17,650 lines
