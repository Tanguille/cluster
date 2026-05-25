# qui consolidation design

## Purpose

Simplify the media download stack by introducing [qui](https://github.com/autobrr/qui) as the primary qBittorrent control plane while preserving the specialized tools that still provide clear value.

The goal is maximum practical simplification, not forcing every workflow into one tool. qui replaces the overlapping qBittorrent hygiene and management role previously owned by qbit_manage, while qBitrr and standalone cross-seed remain because they provide specialized workflows that qui does not fully replace yet.

## Current setup

The current qBittorrent-related stack has four main parts:

| Component | Current role |
| --- | --- |
| `qbittorrent` | Torrent engine and WebUI/API endpoint. |
| `qbit_manage` | Removed. Its previous qBittorrent metadata role moves to qui; its orphan/recyclebin filesystem cleanup is intentionally retired rather than preserved as another controller. |
| `qBitrr` | Arr-driven search/remediation, Jellyseerr request handling, failed/recheck categories, stalled/bad-download cleanup, free-space pause/resume. |
| `cross-seed` | Dedicated cross-seed engine using Prowlarr Torznab feeds, Sonarr/Radarr IDs, qBittorrent injection, hardlinks, partial matching, and season-pack behavior. |

Relevant repository locations:

- `kubernetes/apps/media/qbittorrent/app/helmrelease.yaml`
- `kubernetes/apps/media/qbittorrent/tools/qbitmanage/`
- `kubernetes/apps/media/qbittorrent/tools/qbitrr/`
- `kubernetes/apps/media/cross-seed/`

## qui capabilities to use

qui provides useful overlap with the existing qBittorrent management stack:

- Modern qBittorrent WebUI with multi-instance support.
- Torrent automations for tags, categories, delete actions, share limits, no-hardlink detection, and unregistered states.
- App-level qBittorrent backups and restore previews for torrent metadata, tags, categories, save paths, and cached torrent blobs.
- Built-in cross-seed features with Prowlarr/Jackett, Sonarr/Radarr lookup support, hardlink/reflink modes, RSS scans, library scans, completion-triggered searches, and season-pack assembly.
- Reverse proxy API keys for external clients so Sonarr, Radarr, autobrr, or cross-seed can talk through qui without holding qBittorrent credentials directly.
- Docker deployment via `ghcr.io/autobrr/qui`, default port `7476`, and `/config` persistence.

## Target architecture

The target simplified architecture is:

```text
qBittorrent = torrent engine
qui         = primary UI, qBittorrent hygiene, qBittorrent metadata backups, optional API proxy
qBitrr      = Arr/search/failure/free-space automation
cross-seed  = dedicated cross-seed engine until qui proves parity
```

This removes the broadest overlap without replacing mature specialized behavior prematurely.

## Ownership matrix

| Function | Current owner | Target owner | Notes |
| --- | --- | --- | --- |
| Torrent engine | qBittorrent | qBittorrent | qui does not replace the client. |
| Web UI | qBittorrent | qui | qBittorrent route can remain available during migration. |
| Tracker tagging | qbit_manage | qui | Recreate in qui UI/state. |
| Category updates | qbit_manage | qui | Recreate in qui UI/state. |
| noHL tagging | qbit_manage | qui | qui has hardlink-aware conditions. |
| Unregistered cleanup | qbit_manage | qui | Recreate in qui before enabling destructive behavior. |
| Share limits | qbit_manage | qui | Recreate in qui before enabling destructive behavior. |
| Recyclebin cleanup | qbit_manage | Removed | Intentionally not preserved; avoids keeping a second controller solely for filesystem cleanup. |
| Orphan cleanup | qbit_manage | Removed | Intentionally not preserved; reintroduce later only as a small purpose-built CronJob if needed. |
| Arr request/search automation | qBitrr | qBitrr | qui does not clearly replace Jellyseerr/Arr request and search behavior. |
| Failed/recheck categories | qBitrr | qBitrr | Keep qBitrr as owner. |
| Free-space pause/resume | qBitrr | qBitrr | Current policy targets 500G free space. |
| Stalled/bad-download cleanup | qBitrr | qBitrr | qBitrr has specific sample/trailer/extension/ETA rules. |
| Cross-seed matching/injection | cross-seed | cross-seed initially | Evaluate qui separately before replacing. |
| qBittorrent credential proxying | Direct client credentials | qui proxy API keys, later | Do after qui itself is stable. |

## Kubernetes design

Add qui as a normal media application using the existing `app-template` conventions.

The onedr0p/home-ops qui deployment is a useful reference for the shape of the manifest. It confirms that qui works well as a standalone app-template workload with `/config` persistence, an internal HTTPRoute, a metrics port, a ServiceMonitor, and optional NFS media mounting for local filesystem features.

Suggested layout:

```text
kubernetes/apps/media/qui/
  ks.yaml
  app/
    helmrelease.yaml
    kustomization.yaml
    secret.sops.yaml
```

Recommended deployment shape:

- Image: pin `ghcr.io/autobrr/qui` by version and digest instead of using a floating tag. The home-ops reference currently uses `v1.19.0@sha256:baa07db5326f75f8c2246703603cbe2132476c8ad0ab31c976a960cb4c4731f5`; verify the current upstream release before implementation.
- Port: expose container port `7476` as the internal HTTP service port.
- Route: create an internal HTTPRoute at `qui.${SECRET_DOMAIN}` through `envoy-internal`.
- Persistence: mount a `ceph-block` RWO PVC at `/config`.
- Media mount: mount the same NFS media path qBittorrent sees at `/media` if qui local filesystem features or hardlink-aware checks need filesystem access.
- Database: start with SQLite under `/config`; avoid Postgres unless qui later needs multi-replica or heavier concurrent writes.
- Strategy: use a single replica and avoid multi-writer assumptions.
- Auth: start with `QUI__AUTH_DISABLED: true` and `QUI__I_ACKNOWLEDGE_THIS_IS_A_BAD_IDEA: true` because the route is internal-only through `envoy-internal`; restrict `QUI__AUTH_DISABLED_ALLOWED_CIDRS` to `${LAN_CIDR},${POD_CIDR}`. Keep this explicit so it can be revisited if qui is ever exposed more broadly.
- Secrets: keep session/OIDC/database secrets in SOPS or the existing secret workflow; do not commit plaintext credentials. Use `_FILE` environment variables where qui supports them.
- Security context: follow existing media app patterns with non-root user, dropped capabilities, and only loosen filesystem settings if the container requires it.
- Reloader: annotate the controller with `reloader.stakater.com/auto: "true"` if config or secrets are mounted.
- Metrics: enable qui metrics after the base deployment is stable. home-ops exposes metrics on a separate port with `QUI__METRICS_ENABLED`, `QUI__METRICS_HOST`, `QUI__METRICS_PORT`, and `serviceMonitor.app.endpoints`.

The home-ops reference also suggests adding the storage backup/scaling components at the Flux Kustomization level. In this repo, that means using the existing `volsync` component for the qui config PVC and the existing `nfs-scaler` component if qui mounts the shared media NFS path.

Do not copy home-ops' `zeroscaler` component directly; this repo uses `nfs-scaler` for NFS-backed media workloads.

The first version should not make Sonarr, Radarr, autobrr, or cross-seed depend on qui. The API proxy migration is a separate later phase.

## Migration phases

### Phase 1: Deploy qui without destructive automation

Deploy qui with persistence, auth/session configuration, and an internal route. Connect it to the existing qBittorrent service at `http://qbittorrent.media.svc.cluster.local`.

Success criteria:

- qui starts reliably.
- qui can list torrents, categories, tags, trackers, and save paths.
- qui can take and preview qBittorrent metadata backups.
- If `/media` is mounted, qui can see the same filesystem paths qBittorrent uses for local filesystem and hardlink-aware features.
- No existing automation behavior changes yet.

### Phase 2: Recreate non-destructive qbit_manage behavior in qui

Recreate qbit_manage's low-risk metadata behavior in qui:

- tracker tags
- category rules
- noHL tagging

Because qbit_manage is removed from Git in this consolidation, qui becomes the future owner for these rules. Configure these automations in qui before relying on them operationally.

Success criteria:

- Existing tracker/category/noHL labels remain consistent.
- qbit_manage is no longer installed.
- No unexpected torrent deletion or path changes occur.

### Phase 3: Recreate destructive qbit_manage behavior in qui

After Phase 2 is stable, migrate:

- unregistered torrent deletion
- public/private tracker share-limit rules

Use conservative settings first and validate logs before letting qui delete or alter many torrents.

Success criteria:

- qui actions match the old qbit_manage policy.
- Public tracker cleanup and private tracker seeding policy remain intact.
- qbit_manage remains removed; qui is the only qBittorrent metadata cleanup owner.

### Phase 4: Retire qbit_manage GitOps resources

Remove the qbit_manage Flux Kustomization and manifests from Git.

This consolidation intentionally removes orphan/recyclebin cleanup rather than preserving qbit_manage as a narrowly scoped cleanup job.

Success criteria:

- The `qbitmanage` Flux Kustomization and HelmRelease are removed.
- No broad qbit_manage qBittorrent automation remains.
- Filesystem orphan/recyclebin deletion behavior has no active owner after this PR.

### Phase 5: Evaluate qui cross-seed separately

Do not remove standalone cross-seed during the initial qui migration. Compare qui's cross-seed behavior against the existing cross-seed config.

qui can replace standalone cross-seed only if it matches these requirements:

- same Prowlarr/Jackett indexer coverage
- Sonarr/Radarr ID-assisted matching
- hardlink or reflink behavior compatible with the current downloads layout
- partial matching support appropriate for current usage
- season-pack behavior that matches current policy
- clear logs, retry behavior, and failure visibility

Success criteria:

- qui produces equivalent or better matches in a shadow/comparison period.
- No tracker or path behavior regresses.
- Standalone cross-seed is removed only after parity is proven.

### Phase 6: Optional qui reverse proxy consolidation

After qui is trusted, move qBittorrent API consumers through qui proxy keys one at a time:

1. autobrr
2. cross-seed
3. Sonarr/Radarr

This reduces qBittorrent credential sprawl and centralizes API access, but it should not be coupled to the automation migration.

Success criteria:

- Each client works through its own revocable qui proxy key.
- Direct qBittorrent credentials are removed from client configs where practical.
- Rollback is simple: point the client back to the qBittorrent service.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Overlapping tools delete or retag the same torrent | Maintain the ownership matrix and disable old behavior before enabling equivalent destructive behavior in qui. |
| qui automation does not exactly match qbit_manage behavior | Migrate non-destructive rules first and compare logs/results before destructive rules. |
| qbit_manage orphan/recyclebin behavior is removed | This is intentional for simplification; reintroduce only if a concrete need appears. |
| qBitrr functionality is over-replaced | Keep qBitrr unless qui explicitly supports the same Arr/Jellyseerr/search/free-space workflows. |
| cross-seed parity is incomplete | Keep standalone cross-seed until qui proves equivalent matching, linking, and retry behavior. |
| qBittorrent metadata backups are mistaken for storage backups | Keep VolSync/PVC/NFS backup strategy separate; qui backups are app-level safety nets only. |
| New proxy dependency creates an outage path for Arr/autobrr/cross-seed | Migrate proxy consumers one at a time with clear rollback. |

## Validation plan

Before merging implementation changes:

- Run `mise exec -- kubeconform -strict kubernetes/`.
- Verify SOPS files contain no plaintext secrets.
- Confirm the new app follows existing media namespace and app-template conventions.
- Confirm qbit_manage removal does not leave stale references in `kubernetes/apps/media/kustomization.yaml` or `kubernetes/apps/media/qbittorrent/ks.yaml`.

After deployment, before enabling qui automation behavior:

- Confirm qui can read the qBittorrent instance state.
- Confirm backups can be created and previewed.
- Recreate tracker tags, categories, noHL tagging, unregistered cleanup, and share-limit behavior in qui.
- Confirm no destructive qui rule is active until its affected set is reviewed.

## Rollback plan

The rollout should remain reversible at each phase:

- If qui deployment fails, remove or suspend the `qui` Kustomization; qBittorrent, qBitrr, and cross-seed remain unchanged.
- If qui automation behaves incorrectly, disable the affected qui rule and restore qbit_manage from Git history or implement a smaller replacement job.
- If the proxy path causes client issues, point the affected client back to `qbittorrent.media.svc.cluster.local`.
- If cross-seed parity is insufficient, keep standalone cross-seed and do not migrate that ownership.

## Final recommendation

Implement the maximum-simplification path as a phased consolidation:

1. Add qui as the primary qBittorrent UI/control plane.
2. Recreate qbit_manage metadata automation in qui.
3. Recreate qbit_manage destructive torrent cleanup in qui before enabling it operationally.
4. Retire qbit_manage GitOps resources and intentionally drop orphan/recyclebin cleanup.
5. Keep qBitrr for Arr/search/failure/free-space automation.
6. Keep standalone cross-seed until qui proves exact parity.
7. Use qui proxy API keys later to reduce qBittorrent credential sprawl.

This produces a simpler stack without turning qui into an unsafe big-bang replacement for every specialized workflow.
