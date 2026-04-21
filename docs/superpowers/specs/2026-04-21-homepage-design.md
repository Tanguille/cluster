# Homepage (gethomepage.dev) — Cluster Dashboard Design

**Date:** 2026-04-21
**Status:** Approved — ready for implementation plan
**Scope:** Deploy gethomepage.dev as the cluster's primary dashboard, covering all apps that have a native homepage widget (Tier 1) plus tile-only coverage (Tier 2) for everything else with a UI.

## Summary

Deploy `ghcr.io/gethomepage/homepage` using the bjw-s `app-template` OCI chart (the repo-wide idiom). Discovery is hybrid: each app's `HTTPRoute` carries `gethomepage.dev/*` annotations that own its tile identity and widget wiring, while a central ConfigMap owns global UX (settings, layout/tabs, bookmarks, info widgets, kubernetes integration). Exposure is internal-only via `envoy-internal`. No persistent volumes — config is declarative, logs stream to an `emptyDir`.

## Goals

- Single internal landing page at `home.${SECRET_DOMAIN}` with six topical tabs.
- Native API-integrated widgets on all 19 apps with a matching homepage widget type.
- Tile-only entries for apps with a UI but no widget (~23 apps).
- Live cluster stats in the header (kromgo-fed `customapi` + built-in `kubernetes`/`resources` widgets).
- Zero empty stub config files. Minimal RBAC. Stateless.
- Match this repo's conventions: `app-template` chart, `ks.yaml` + `app/` layout, SOPS secrets, Reloader annotation, Renovate pins, Kustomize `configMapGenerator`.

## Non-goals

- No external exposure (no `envoy-external` route, no Cloudflare Access integration). Access is LAN/VPN only.
- No SSO / auth proxy. Homepage itself has no auth; internal-only placement is the containment.
- No rook-ceph dashboard integration (no existing HTTPRoute; would require separate routing work).
- No cloudflared widget (requires external CF API token for minor metrics; low ROI; tunnel health is already covered by Gatus).
- No persistent storage. No volsync.
- No Prometheus `ServiceMonitor` — homepage has no metrics endpoint.
- No additions to `web3`, `database`, or infrastructure operator namespaces beyond what's already deployed.

## Architecture

| Component | Choice |
|---|---|
| Chart | `oci://ghcr.io/bjw-s-labs/helm-charts/app-template` (already referenced repo-wide) |
| Image | `ghcr.io/gethomepage/homepage`, pinned + Renovate-managed |
| Namespace | `default` (sits alongside nextcloud/immich/searxng/karakeep — fellow user-facing portals) |
| Gateway | `envoy-internal` (internal DNS, LAN/VPN only) |
| Hostname | `home.${SECRET_DOMAIN}` |
| Reload | `reloader.stakater.com/auto: "true"` on the controller — auto-restart on ConfigMap/Secret change |
| State | Stateless. ConfigMap RO mount at `/app/config`; emptyDir at `/app/config/logs` |
| Discovery | Hybrid — annotations per-app on existing `HTTPRoute` + central ConfigMap for global UX |
| Secrets | SOPS-age encrypted `Secret` mounted via `envFrom`, keys referenced as `{{HOMEPAGE_VAR_*}}` |

## File layout

```
kubernetes/apps/default/homepage/
├── ks.yaml                              # Flux Kustomization, postBuild substitute APP
└── app/
    ├── kustomization.yaml               # configMapGenerator (disableNameSuffixHash:true) for 5 config files → homepage-configmap
    ├── helmrelease.yaml                 # bjw-s app-template HR
    ├── secret.sops.yaml                 # HOMEPAGE_VAR_* widget credentials
    └── resources/
        ├── settings.yaml                # theme, layout (tabs + groups), columns
        ├── bookmarks.yaml               # Home-tab Documentation + Repositories groups
        ├── widgets.yaml                 # global header: info widgets + kromgo customapi
        ├── kubernetes.yaml              # mode: cluster
        └── services.yaml                # static entries ONLY for TLSRoute services
                                         # (TrueNAS, OPNsense, IPMI) that can't be discovered
```

Entry in `kubernetes/apps/default/kustomization.yaml`: `- ./homepage/ks.yaml`.

## Discovery & annotation schema

Each Tier 1 app gains `gethomepage.dev/*` annotations on its existing `HTTPRoute`'s `metadata.annotations` block. The `route.<name>.annotations` field in bjw-s `app-template` propagates to the generated HTTPRoute (the pattern already used by Gatus's `gatus.home-operations.com/*` annotations).

**Required annotations (all tiers):**

```yaml
gethomepage.dev/enabled: "true"
gethomepage.dev/name: "<Display Name>"
gethomepage.dev/description: "<one-line description>"
gethomepage.dev/group: "<layout group>"   # matches a key in settings.yaml layout
gethomepage.dev/icon: "<name>.png"        # selfh.st icon set (default resolver)
```

**Additional annotations for Tier 1 (widget-enabled):**

```yaml
gethomepage.dev/widget.type: "<widget type>"
gethomepage.dev/widget.url: "http://<svc>.<ns>.svc.cluster.local[:<port>]"
gethomepage.dev/widget.key: "{{HOMEPAGE_VAR_<APP>_API_KEY}}"
# widget-specific additions (e.g. widget.fields, widget.username) as needed
```

**Rationale for in-cluster URLs in widget.url:** homepage's widget polling stays inside the cluster, bypassing the Envoy gateway, Cloudflare, and external DNS. Eliminates dependency on external cert validity and avoids unnecessary egress. The tile's click-through href (derived from the HTTPRoute hostname) still uses the external URL.

**Non-discoverable services (TLSRoute):** TrueNAS, OPNsense, IPMI live behind `envoy-internal-tls` with `TLSRoute`. Homepage k8s discovery only reads `Ingress`, `HTTPRoute`, and Traefik `IngressRoute` — never `TLSRoute`. These three are defined statically in `services.yaml` under the Infra tab's "External" group.

## Tab topology

| Tab | Layout groups | Source |
|---|---|---|
| **Home** | `Documentation`, `Repositories` | `bookmarks.yaml`, `tab: Home` on each group |
| **Media** | `Requests`, `Library`, `Downloaders`, `Management`, `Processing` | Annotations on media/* HTTPRoutes |
| **AI** | `Interfaces`, `Models`, `Agents` | Annotations on ai/* HTTPRoutes |
| **Apps** | `Portals`, `Utilities`, `Web3` | Annotations on default/* HTTPRoutes + annotations on `web3/monero/dashboard` HTTPRoute (`p2pool.${SECRET_DOMAIN}`); `monerod`/`p2pool`/`xmrig` sub-apps have no UI and are not tiled |
| **Observability** | `Status`, `Metrics`, `Logs` | Annotations on observability/* HTTPRoutes |
| **Infra** | `Security`, `External` | Annotations on security/* + static TLSRoute entries |

Info widgets in `widgets.yaml` render in the **global header** on every tab — they are not tab-scoped. The Home tab therefore contains only bookmarks (with optional future Quick-Access service duplicates if desired).

Layout-level settings: `maxGroupColumns: 6`, `fullWidth: true`, `useEqualHeights: true`, `theme: dark`, `color: slate`.

## Global widgets (header, `widgets.yaml`)

Ordered list of info widgets visible on every tab:

1. `greeting` — `text_size: xl`, text: "Cluster"
2. `datetime` — long date, short time
3. `search` — provider: `searxng`, url: `https://searxng.${SECRET_DOMAIN}`
4. `kubernetes` — cluster + per-node CPU/mem rollups (needs `metrics.k8s.io` RBAC)
5. `resources` — cluster-aggregate CPU + memory
6. `customapi` × 4 — kromgo-sourced: `cluster_uptime_days`, `cluster_node_count`, `cluster_pod_count`, `cluster_alert_count`. URL: `https://kromgo.${SECRET_DOMAIN}/<metric>`; mappings on the `value` field. Gives a permanent "cluster status" strip that mirrors the README badges.

## Widget & tile inventory

### Tier 1 — API-integrated widgets (19 apps)

| Namespace | App | Widget type | Group (tab) | Required secrets |
|---|---|---|---|---|
| media | sonarr | sonarr | Management (Media) | `SONARR_API_KEY` |
| media | radarr | radarr | Management (Media) | `RADARR_API_KEY` |
| media | bazarr | bazarr | Management (Media) | `BAZARR_API_KEY` |
| media | prowlarr | prowlarr | Management (Media) | `PROWLARR_API_KEY` |
| media | qbittorrent | qbittorrent | Downloaders (Media) | `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` |
| media | jellyfin | jellyfin | Library (Media) | `JELLYFIN_API_KEY` |
| media | jellystat | jellystat | Library (Media) | `JELLYSTAT_API_KEY` |
| media | seerr | jellyseerr | Requests (Media) | `JELLYSEERR_API_KEY` |
| media | unpackerr | unpackerr | Processing (Media) | — (URL only) |
| media | fileflows | fileflows | Processing (Media) | `FILEFLOWS_API_KEY` |
| ai | ollama | ollama | Models (AI) | — (URL only) |
| default | immich | immich | Portals (Apps) | `IMMICH_API_KEY` |
| default | nextcloud | nextcloud | Portals (Apps) | `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD` |
| default | searxng | searxng | Utilities (Apps) | — |
| default | changedetection | changedetectionio | Utilities (Apps) | `CHANGEDETECTION_API_KEY` |
| observability | grafana | grafana | Metrics (Observability) | `GRAFANA_USERNAME`, `GRAFANA_PASSWORD` |
| observability | kube-prometheus-stack | prometheus | Metrics (Observability) | — (URL only; unauth within cluster) |
| observability | gatus | gatus | Status (Observability) | — |
| security | crowdsec | crowdsec | Security (Infra) | `CROWDSEC_USERNAME`, `CROWDSEC_PASSWORD` |

### Tier 2 — tile-only (~23 apps, no widget)

- **AI (5):** open-webui, llama-server, opencode, moltis, toolhive
- **Apps (5):** karakeep, picoshare, it-tools, dumbassets, spoolman
- **Media (7):** flaresolverr, recyclarr, cross-seed, deduparr, streamystats, brrpolice, wizarr
- **Observability (2):** victoria-logs, kromgo
- **Web3 (1):** monero (only the `dashboard` sub-app; `monerod`/`p2pool`/`xmrig` have no UI)
- **External (static in services.yaml — 3):** truenas, opnsense, ipmi

### Omitted (no UI / operators / agents)

`cert-manager`, `flux-system`, `kube-system`, `openebs-system`, `rook-ceph` (operator + cluster), `volsync-system`, `actions-runner-system`, `system-upgrade`, `network/envoy-gateway`, `network/external-dns`, `network/k8s-gateway`, `network/cloudflared`, `observability/kepler`, `observability/keda`, `observability/silence-operator`, `observability/siren`, `observability/fluent-bit`, `observability/exporters`, `database/dragonfly`, `database/cloudnative-pg`.

Cluster-level resource usage for these is visible via the `kubernetes` and `resources` info widgets in the header.

## Secrets model (`secret.sops.yaml`)

A single SOPS-encrypted `Secret` named `homepage-secret` in namespace `default`, mounted via `envFrom` on the app container. Variables referenced in `gethomepage.dev/widget.*` annotations as `{{HOMEPAGE_VAR_<NAME>}}`.

Expected keys (stringData, pre-encryption):

```
HOMEPAGE_VAR_SONARR_API_KEY
HOMEPAGE_VAR_RADARR_API_KEY
HOMEPAGE_VAR_BAZARR_API_KEY
HOMEPAGE_VAR_PROWLARR_API_KEY
HOMEPAGE_VAR_QBITTORRENT_USERNAME
HOMEPAGE_VAR_QBITTORRENT_PASSWORD
HOMEPAGE_VAR_JELLYFIN_API_KEY
HOMEPAGE_VAR_JELLYSTAT_API_KEY
HOMEPAGE_VAR_JELLYSEERR_API_KEY
HOMEPAGE_VAR_FILEFLOWS_API_KEY
HOMEPAGE_VAR_IMMICH_API_KEY
HOMEPAGE_VAR_NEXTCLOUD_USERNAME
HOMEPAGE_VAR_NEXTCLOUD_PASSWORD
HOMEPAGE_VAR_CHANGEDETECTION_API_KEY
HOMEPAGE_VAR_GRAFANA_USERNAME
HOMEPAGE_VAR_GRAFANA_PASSWORD
HOMEPAGE_VAR_CROWDSEC_USERNAME
HOMEPAGE_VAR_CROWDSEC_PASSWORD
```

## Volumes & mount strategy

```yaml
persistence:
  config:
    type: configMap
    name: homepage-configmap        # produced by Kustomize configMapGenerator
    globalMounts:
      - path: /app/config           # whole-dir RO mount — no subPath per file
  logs:
    type: emptyDir
    globalMounts:
      - path: /app/config/logs      # overlays RO parent; homepage writes log files here
```

**Rationale:** whole-dir RO mount hides the container image's baked-in skeleton configs, preventing the "sample Sonarr/Radarr tiles appear on first boot" pollution that per-file `subPath` mounts suffer from. Only five files exist in `/app/config/` — no empty stubs needed. The `logs` overlay is required because homepage writes log files to `/app/config/logs/` and the config mount is read-only; `LOG_TARGETS=stdout` is not documented in the official installation guide and is not relied upon.

## Environment

```yaml
env:
  TZ: ${TIMEZONE}
  MY_POD_IP:
    valueFrom:
      fieldRef:
        fieldPath: status.podIP
  HOMEPAGE_ALLOWED_HOSTS: $(MY_POD_IP):3000,home.${SECRET_DOMAIN}
envFrom:
  - secretRef:
      name: homepage-secret
```

`HOMEPAGE_ALLOWED_HOSTS` must include the pod IP with port to satisfy the k8s liveness probe (per upstream installation guide). Comma-separated, no spaces.

## RBAC

Single `ClusterRole` + `ClusterRoleBinding` via bjw-s's `rbac:` values. No dead api groups.

```yaml
rbac:
  roles:
    homepage:
      type: ClusterRole
      rules:
        - apiGroups: [""]
          resources: [namespaces, pods, nodes, services]
          verbs: [get, list]
        - apiGroups: [gateway.networking.k8s.io]
          resources: [httproutes, gateways]
          verbs: [get, list]
        - apiGroups: [metrics.k8s.io]
          resources: [nodes, pods]
          verbs: [get, list]
  bindings:
    homepage:
      type: ClusterRoleBinding
      roleRef:
        identifier: homepage
      subjects:
        - identifier: homepage
serviceAccount:
  homepage: {}
```

**Deliberately omitted:** `extensions/ingresses`, `networking.k8s.io/ingresses`, `traefik.containo.us/ingressroutes`, `traefik.io/ingressroutes` — repo has zero `Ingress` or Traefik resources (verified), so these rules would be dead weight.

## Look & feel (`settings.yaml`)

```yaml
title: Cluster
theme: dark
color: slate
target: _blank
headerStyle: clean
hideVersion: true
statusStyle: dot
fullWidth: true
maxGroupColumns: 6
useEqualHeights: true
providers:
  longhorn: false        # not used
layout:
  # Home tab (bookmarks-only)
  Documentation:     { tab: Home,          style: row, columns: 4, header: true }
  Repositories:      { tab: Home,          style: row, columns: 3, header: true }
  # Media tab
  Requests:          { tab: Media,         style: row, columns: 2 }
  Library:           { tab: Media,         style: row, columns: 3 }
  Downloaders:       { tab: Media,         style: row, columns: 2 }
  Management:        { tab: Media,         style: row, columns: 4 }
  Processing:        { tab: Media,         style: row, columns: 3 }
  # AI tab
  Interfaces:        { tab: AI,            style: row, columns: 3 }
  Models:            { tab: AI,            style: row, columns: 3 }
  Agents:            { tab: AI,            style: row, columns: 3 }
  # Apps tab
  Portals:           { tab: Apps,          style: row, columns: 4 }
  Utilities:         { tab: Apps,          style: row, columns: 4 }
  Web3:              { tab: Apps,          style: row, columns: 3 }
  # Observability tab
  Status:            { tab: Observability, style: row, columns: 2 }
  Metrics:           { tab: Observability, style: row, columns: 3 }
  Logs:              { tab: Observability, style: row, columns: 3 }
  # Infra tab
  Security:          { tab: Infra,         style: row, columns: 3 }
  External:          { tab: Infra,         style: row, columns: 3 }
```

## Kubernetes integration (`kubernetes.yaml`)

```yaml
mode: cluster
```

Uses in-cluster ServiceAccount credentials via the RBAC above. `cluster` mode aggregates per-namespace resource usage for the `kubernetes` info widget and enables annotation-based discovery.

## Delivery & automation

- **Flux Kustomization** (`ks.yaml`): standard pattern with `postBuild.substitute.APP: homepage`, `prune: true`, `wait: false`, `interval: 30m`, `retryInterval: 1m`. No volsync component.
- **HelmRelease chart ref:** `chartRef.kind: OCIRepository`, `name: app-template` (referenced from `components/common/repos/app-template`).
- **Renovate:** `# renovate: datasource=docker depName=ghcr.io/gethomepage/homepage` on the image tag line.
- **Reloader:** `reloader.stakater.com/auto: "true"` on the controller template → ConfigMap + Secret changes trigger rolling restarts automatically.
- **`kubeconform -strict kubernetes/`** must pass before commit.
- **`flux-local test`** (if run) should reconcile cleanly.

## Annotation changes to existing apps

For each Tier 1 app, a single edit to its existing `app/helmrelease.yaml` adds `route.<name>.annotations` (or extends existing annotations). This is 19 targeted edits to existing files — no new files per app, no new ks.yaml entries per app. One flag per app: the annotation block.

**Tier 2 tile-only apps** get the same annotation block minus `widget.*` keys.

## Out of scope / future work

- External exposure via `envoy-external` + Cloudflare Access (if remote access becomes needed).
- Rook-Ceph dashboard tile — requires adding a new HTTPRoute for `rook-ceph-mgr-dashboard` (scoped separately).
- Cloudflared widget — requires Cloudflare API token provisioning (skipped; Gatus covers tunnel health).
- Glances / Uptime Kuma integration — redundant with existing Gatus and the `resources` info widget.
- Custom theming (`custom.css`/`custom.js`) — default theme is sufficient; add later if desired.
- Additional Quick-Access service duplicates on the Home tab — can be layered in post-deploy once real usage patterns emerge.

## Process instructions

- After completing each step, update the implementation plan with current status.
- Pause for user confirmation before proceeding to the next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the spec and plan contents are consolidated into existing documentation (or the homepage install is complete and self-documenting via its ConfigMap), the plan file can be removed. The spec itself is a reference document and stays.
- Every prompt should verify the branch and worktree before doing any work.
