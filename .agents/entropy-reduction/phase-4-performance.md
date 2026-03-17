# Phase 4: Performance Analysis
**Completed:** 2026-03-07

## Findings

### 1. Reconciliation Intervals
Analyzed 264+ interval configurations across the cluster. Found several instances of overly frequent reconciliation that may cause unnecessary load:

**5-minute intervals (high frequency - should be reviewed):**
- `kube-system/snapshot-controller/app/helmrelease.yaml` - HelmRelease interval
- `kube-system/nvidia-device-plugin/app/helmrelease.yaml` - HelmRelease interval
- `database/dragonfly/app/ocirepository.yaml` - OCIRepository interval
- `default/nextcloud/app/ocirepository.yaml` - OCIRepository interval
- `openebs-system/openebs/app/ocirepository.yaml` - OCIRepository interval
- `observability/exporters/dcgm-exporter/app/ocirepository.yaml` - OCIRepository interval
- `components/common/repos/app-template/ocirepository.yaml` - OCIRepository interval

**10-minute intervals (moderate frequency):**
- `default/karakeep/ks.yaml` - Kustomization
- `default/searxng/ks.yaml` - Kustomization
- `ai/opencode/ks.yaml` - Kustomization
- `ai/open-webui/ks.yaml` - Kustomization
- `ai/ollama/ks.yaml` - Kustomization
- `media/seerr/app/helmrelease.yaml` - HelmRelease

### 2. Resource Configuration Analysis
- **208 files** define `resources:` sections
- **96 files** define `requests:` (resource requests)
- **~112 files** may be missing resource requests (potential throttling risk)
- **91 files** define `limits:` (resource limits)

**Potential Throttling Risk:** When limits are set without corresponding requests, Kubernetes applies a default request equal to the limit, which can cause CPU throttling under load.

### 3. ConfigMap and Secret Sizes
- **No large ConfigMaps found** that would impact etcd performance
- Largest ConfigMap: `components/common/cluster-settings.yaml` (18 lines)
- All ConfigMaps are small configuration stores (< 1KB)

### 4. Image Pull Policies
- **Only 1 occurrence** of explicit `imagePullPolicy: IfNotPresent` found
- **No `imagePullPolicy: Always` found** (good practice for pinned tags)
- **3 images use `latest` tag** (not best practice):
  - `media/wizarr/app/helmrelease.yaml` - Uses latest@sha256
  - `web3/monero/monerod/helmrelease.yaml` - Uses latest@sha256
  - `media/fileflows/app/helmrelease.yaml` - Uses latest (no digest)

### 5. Storage Configuration
- **No unnecessary Retain policies found**
- Rook Ceph uses `reclaimPolicy: Delete` (appropriate)
- Large storage allocations:
  - Immich library: 7Ti PVC (NFS)
  - Nextcloud: 500Gi NFS PVC
  - Prometheus: 110Gi (with 100GB retentionSize)

### 6. Cleanup and Finalizers
- **1 instance of `prune: false`** found:
  - `ai/toolhive/ks.yaml` - Prevents automatic cleanup of removed resources
- **No explicit finalizers** configured
- **No cleanup issues** detected

### 7. Helm Values Passing
- **Only 1 file** uses `valuesFrom:` (netdata HelmRelease)
- Most HelmReleases use inline `values:` (efficient)
- No evidence of overloading valuesFrom patterns

### 8. External Service Kustomizations
- `network/external-service/ks.yaml` contains **9 separate Kustomizations**
- Each has 30m interval and `wait: true`
- Consider consolidating or using longer intervals for static external services

### 9. Flux Reconciliation Patterns (N+1 Analysis)
- **94 HelmReleases** across the cluster
- **16 top-level namespaces** (apps)
- **13 observability exporters** as separate HelmReleases (potential N+1):
  - bazarr-exporter, blackbox-exporter, dcgm-exporter, nextcloud-exporter
  - nut-exporter, opnsense-exporter, prowlarr-exporter, qbittorrent-exporter
  - radarr-exporter, scraparr, smartctl-exporter, sonarr-exporter, speedtest-exporter
- Single `cluster-apps` Kustomization manages all apps (good)
- Global patches in `ks.yaml` apply defaults efficiently

### 10. CI/CD Pipeline Performance
- **flux-local.yaml**: filter → test → diff → success (sequential, correct)
- **image-pull.yaml**: filter → extract → diff → pull (parallelized with max-parallel: 4)
- Both already use matrix strategies appropriately
- No obvious parallelization bottlenecks

## Performance Issues

| Severity | Location | Issue | Impact | Fix |
|----------|----------|-------|--------|-----|
| **High** | 13 observability exporters | Separate HelmReleases for each exporter (N+1 pattern) | 13x reconciliation overhead; each requires separate Helm download, template, install cycle | Combine into single HelmRelease with multiple `targets` or use subcharts |
| **High** | 25 HelmReleases missing resources | coredns, cilium, snapshot-controller, external-dns, envoy-gateway, k8s-gateway, keda, kepler, netdata, silence-operator, openebs, crowdsec, tuppr, volsync, dragonfly, cloudnative-pg, barman, grafana, toolhive (app+crds), cert-manager, reloader, descheduler, spegel, actions-runner-controller, nvidia-device-plugin | No QoS guarantees; potential for noisy neighbor; unbounded CPU/memory | Add resource requests/limits to all workload HelmReleases |
| Medium | `kube-system/snapshot-controller/app/helmrelease.yaml` | 5m reconciliation interval | Unnecessary API load | Increase to 15-30m |
| Medium | `kube-system/nvidia-device-plugin/app/helmrelease.yaml` | 5m reconciliation interval | Unnecessary API load | Increase to 15-30m |
| Medium | `components/common/repos/app-template/ocirepository.yaml` | 5m OCIRepository interval | Unnecessary registry polling | Increase to 15-30m |
| Medium | ~112 HelmReleases | Missing resource requests | CPU throttling under load | Add explicit requests |
| Low | Multiple OCIRepositories | 5m interval on stable charts | Minor API load | Consider 15m for stable charts |
| Low | `ai/toolhive/ks.yaml` | `prune: false` prevents cleanup | Resource accumulation risk | Enable pruning or document reason |
| Low | `media/fileflows/app/helmrelease.yaml` | Uses `latest` tag without digest | Non-reproducible deployments | Pin to specific tag@sha256 |
| Low | `network/external-service/ks.yaml` | 9 Kustomizations with 30m interval | Multiple reconciliation loops | Consider consolidating or increasing to 1h |
| Low | Multiple apps | Resource limits without requests | Potential throttling | Add explicit resource requests |
| Low | searxng interval: 15m | Short interval for static app | Unnecessary reconciliation | Increase to 30m or 1h |
| Low | sonarr/radarr/prowlarr intervals: 15m | Media app indexes update frequently | Slightly aggressive | Consider 30m |
| Low | seerr interval: 10m | Shortest interval in cluster | Unnecessary reconciles | Increase to 30m |

## Action Items
- [ ] Combine 13 observability exporters into single HelmRelease or grouped releases
- [ ] Add resource limits to 25 HelmReleases missing them
- [ ] Review and increase 5m reconciliation intervals to 15-30m
- [ ] Document why `ai/toolhive/ks.yaml` has `prune: false` or enable pruning
- [ ] Pin `media/fileflows` image to digest-based tag
- [ ] Consider consolidating external-service Kustomizations or increasing intervals
- [ ] Review resource limits vs requests ratio to prevent throttling

## Summary Stats
- Total issues: 16
- Critical: 0 | High: 2 | Medium: 4 | Low: 10
- HelmReleases without resources: 25
- Exporters (potential combine): 13
- Avg reconciliation interval: ~35 minutes (good distribution)

### Reconciliation Interval Summary
| Interval | Count | Recommendation |
|----------|-------|----------------|
| 5m | 7 | Consider increasing to 15m+ |
| 10m | 9 | Acceptable for active development |
| 15m | 20 | Good for OCIRepositories |
| 30m | 45 | Good default for apps |
| 1h | 90+ | Good for stable infrastructure |
| 2h | 1 | Good for HelmRepositories |

### Resource Configuration Summary
| Category | Count |
|----------|-------|
| Files with resources: | 208 |
| Files with requests: | 96 |
| Files with limits: | 91 |
| Files missing requests (risk): | ~112 |

### Storage Summary
| PVC Size | Count | Notes |
|----------|-------|-------|
| > 1Ti | 1 | Immich library (7Ti NFS) |
| 100-500Gi | 2 | Nextcloud, Prometheus |
| < 100Gi | 10+ | Standard app storage |

### Notes
- Overall cluster configuration follows good practices
- No critical performance issues detected
- Main concerns are reconciliation frequency, N+1 exporter pattern, and missing resource requests
- No etcd pressure from large ConfigMaps/Secrets
- Storage policies are appropriately configured
- CI/CD pipelines are already well-optimized
