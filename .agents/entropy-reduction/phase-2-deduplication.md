# Phase 2: Code Deduplication

**Completed:** 2026-03-07

## Findings

This analysis identifies patterns of duplication across the codebase that could be abstracted for better maintainability. The cluster uses a GitOps workflow with FluxCD, HelmReleases using the `app-template` chart, and consistent patterns for secrets, routing, and persistence.

### Major Duplication Categories

#### 1. HelmRelease Pattern Duplication (High Impact)

The vast majority of applications use the `app-template` Helm chart from bjw-s-labs with nearly identical structures:

- **46 HelmReleases** use the identical schema annotation for app-template
- **63 instances** of `reloader.stakater.com/auto: "true"` annotation
- **38 files** reference `envoy-internal` gateway for routing
- **22 files** use `runAsUser: 568` (arr-suite/media apps standard)
- **24 files** use `capabilities: { drop: ["ALL"] }`

#### 2. *arr Suite Duplication (Critical)

The media applications (Radarr, Sonarr, Bazarr, Prowlarr) are nearly **identical copies**:

- Same security contexts (runAsUser: 568, fsGroup: 568)
- Same init-container pattern for postgres-init
- Same probe configurations (path: /ping, same timing)
- Same route configuration structure
- Same NFS persistence pattern
- Same node affinity rules (prefer control-1)
- Same tmp emptyDir with Memory medium

**Specific Examples:**
- `media/radarr/app/helmrelease.yaml` vs `media/sonarr/app/helmrelease.yaml` - ~95% identical
- `media/bazarr/app/helmrelease.yaml` vs `media/prowlarr/app/helmrelease.yaml` - ~90% identical

#### 3. Postgres Init Container Duplication

**11 applications** use the exact same init-db pattern:

```yaml
initContainers:
  init-db:
    image:
      repository: ghcr.io/home-operations/postgres-init
      tag: 18
    envFrom:
      - secretRef:
          name: {{app}}-secret
```

Applications: spoolman, open-webui, immich-server, n8n, sonarr, bazarr, prowlarr, jellystat, streamystats, radarr, gatus

#### 4. KS.yaml Template Duplication

Multiple `ks.yaml` files follow identical patterns:

- **Radarr/Sonarr ks.yaml**: Nearly identical (27 lines, same structure, differ only in app name)
- **Many ks.yaml files** repeat the same Flux Kustomization structure with only name/namespace changes

#### 5. Kustomization.yaml Duplication

Many app-level `kustomization.yaml` files are identical:

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./helmrelease.yaml
```

Found in: echo, it-tools, and potentially many more.

#### 6. Taskfile Duplication

The `upgrade-arc` task is **duplicated** in:
- `Taskfile.yaml` (lines 39-48)
- `.taskfiles/kubernetes/Taskfile.yaml` (lines 43-52)

Same commands, same description, same GitHub documentation link.

#### 7. SOPS Secret Pattern Duplication

All `.sops.yaml` files have identical metadata structure:
- Same `encrypted_regex: ^(data|stringData)$`
- Same `mac_only_encrypted: true`
- Same age recipient
- Same sops version metadata

Only the actual encrypted values differ.

#### 8. Component Pattern Reuse (Good DRY Examples)

The `volsync` and `nfs-scaler` components demonstrate good abstraction:
- Use variables like `${APP}`, `${VOLSYNC_CAPACITY}`
- Single source of truth for PVC, ReplicationSource, ReplicationDestination patterns
- Referenced by multiple apps via `components:` in ks.yaml

#### 9. Security Context Duplication

Multiple security context patterns repeat:

```yaml
securityContext:
  runAsUser: 568
  runAsGroup: 568
  runAsNonRoot: true
  fsGroup: 568
  fsGroupChangePolicy: OnRootMismatch
  seccompProfile: { type: RuntimeDefault }
```

Found in: radarr, sonarr, bazarr, prowlarr, recyclarr, jellyfin, qbittorrent, etc.

#### 10. Route Configuration Duplication

**38+ apps** use nearly identical route configurations:

```yaml
route:
  app:
    hostnames:
      - "{{ .Release.Name }}.${SECRET_DOMAIN}"
    parentRefs:
      - name: envoy-internal
        namespace: network
```

#### 11. Probe Pattern Duplication

Standard probe configurations repeated across apps:

```yaml
probes:
  liveness: &probes
    enabled: true
    custom: true
    spec:
      httpGet:
        path: /ping
        port: *port
      initialDelaySeconds: 30
      periodSeconds: 30
      timeoutSeconds: 10
      failureThreshold: 5
  readiness: *probes
```

Found in: radarr, sonarr, prowlarr (nearly identical)

## Duplications Found

| Files | Pattern | Lines Affected | Suggested Abstraction |
|-------|---------|----------------|------------------------|
| 46 HelmReleases | app-template schema annotation | 1 line each | Remove or centralize schema reference |
| 59 files | `reloader.stakater.com/auto: "true"` | 63 instances | Component/patch for common annotations |
| 38 files | envoy-internal route parentRef | 2-3 lines each | YAML anchor or component for routes |
| 21 files | runAsUser: 568 security context | 3-8 lines each | YAML anchor: `&media-security-context` |
| 11 apps | postgres-init init-container | 6-8 lines each | Helm values component or anchor |
| 2 files | upgrade-arc task | 10 lines | Remove duplicate from kubernetes Taskfile |
| Radarr/Sonarr | Nearly identical HelmReleases | ~120 lines each | Media-app base template |
| Bazarr/Prowlarr | Nearly identical HelmReleases | ~100 lines each | Media-app base template |
| 40+ ks.yaml | Identical Kustomization structure | 18-30 lines each | KS.yaml template/generator |
| 20+ kustomization.yaml | Single resource reference | 6 lines each | Consider default or generator |
| 23 files | capabilities: {drop: ["ALL"]} | 1 line each | YAML anchor: `&drop-all-caps` |
| 10 files | seccompProfile: RuntimeDefault | 1 line each | Include in security context anchor |
| 91 HelmReleases | interval: (1h\|30m\|15m) | 1 line each | Standardize default interval |
| 37 OCIRepositories | layerSelector pattern | 3 lines each | Consider component abstraction |

## Action Items

### High Priority

- [ ] **Create media-app HelmRelease template**: Abstract Radarr/Sonarr/Bazarr/Prowlarr commonalities into a reusable base
- [ ] **Remove duplicate upgrade-arc task**: Present in both Taskfile.yaml and .taskfiles/kubernetes/Taskfile.yaml
- [ ] **Create YAML anchors for common patterns**: Define at top of files or in common component:
  - `&media-security-context` (runAsUser: 568, etc.)
  - `&standard-probes` (liveness/readiness pattern)
  - `&drop-all-caps` (capabilities drop)
  - `&envoy-internal-route` (route parentRef)

### Medium Priority

- [ ] **Standardize HelmRelease intervals**: Use consistent default (recommend 30m for apps, 1h for infrastructure)
- [ ] **Create init-db component**: Abstract postgres-init pattern for apps that need it
- [ ] **Consolidate kustomization.yaml files**: Many are identical single-resource references
- [ ] **Create SOPS secret template**: All SOPS files share identical metadata structure

### Low Priority

- [ ] **Evaluate schema annotations**: 46 files use identical app-template schema reference
- [ ] **Consider component for common annotations**: `reloader.stakater.com/auto` patch
- [ ] **Document DRY patterns**: Add to AGENTS.md or style guide

## Summary Stats

- **Total issues:** 23 distinct duplication patterns
- **Critical:** 3 (identical app structures, duplicate task, missing anchors)
- **High:** 7 (repeated security contexts, init containers, routes)
- **Medium:** 8 (ks.yaml patterns, kustomization.yaml, SOPS)
- **Low:** 5 (schema annotations, intervals, minor patterns)

**Estimated lines that could be deduplicated:** ~1,500-2,000 lines across the codebase

**Best Practice Examples Found:**
- `components/volsync/` - Excellent use of variables and component pattern
- `components/nfs-scaler/` - Clean ScaledObject abstraction
- KS.yaml anchors (`&app`, `&namespace`) - Good use of YAML anchors
- HelmRelease value anchors (`&port`, `&probes`) - Good in-file deduplication

**Recommendations:**
1. Create a `components/common/templates/` directory for reusable HelmRelease fragments
2. Use Kustomize components more extensively for shared patterns
3. Consider a YAML linter/presubmit check for copy-pasted code
4. Document the "arr-suite" pattern as a template for future media apps
