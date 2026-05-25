# qui Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy qui as the qBittorrent control plane and remove the no-longer-needed qbit_manage GitOps resources.

**Architecture:** Add qui as a media namespace app-template workload with `/config` persistence, `/media` read-only NFS access, internal HTTPRoute, metrics, and auth disabled for trusted LAN/pod CIDRs because the route is internal-only. Keep qBittorrent, qBitrr, and cross-seed in place; remove qbit_manage immediately as part of this simplification and recreate needed qBittorrent hygiene rules in qui UI/state.

**Tech Stack:** FluxCD Kustomizations, bjw-s app-template HelmRelease, HTTPRoute via `envoy-internal`, VolSync, nfs-scaler, SOPS, kubeconform.

---

## File structure

Create:

- `kubernetes/apps/media/qui/ks.yaml` — Flux Kustomization for qui, with VolSync and NFS scaler components.
- `kubernetes/apps/media/qui/app/kustomization.yaml` — Kustomize app entrypoint.
- `kubernetes/apps/media/qui/app/helmrelease.yaml` — app-template HelmRelease for qui.

Modify:

- `kubernetes/apps/media/kustomization.yaml` — register `./qui/ks.yaml`.
- `kubernetes/apps/media/qbittorrent/ks.yaml` — remove the qbitmanage Flux Kustomization document.

Delete:

- `kubernetes/apps/media/qbittorrent/tools/qbitmanage/config/config.yaml`
- `kubernetes/apps/media/qbittorrent/tools/qbitmanage/helmrelease.yaml`
- `kubernetes/apps/media/qbittorrent/tools/qbitmanage/kustomization.yaml`

Do not modify yet:

- `kubernetes/apps/media/qbittorrent/app/helmrelease.yaml` — qBittorrent remains the torrent engine.
- `kubernetes/apps/media/qbittorrent/tools/qbitrr/` — qBitrr remains owner of Arr/search/failure/free-space behavior.
- `kubernetes/apps/media/cross-seed/` — standalone cross-seed remains owner until qui proves parity.

---

### Task 1: Add the qui Flux Kustomization

**Files:**
- Create: `kubernetes/apps/media/qui/ks.yaml`

- [ ] **Step 1: Create the directory**

Run:

```bash
mkdir -p kubernetes/apps/media/qui/app
```

Expected: command exits 0 and creates `kubernetes/apps/media/qui/app`.

- [ ] **Step 2: Add the Flux Kustomization**

Create `kubernetes/apps/media/qui/ks.yaml` with:

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app qui
  namespace: &namespace media
spec:
  path: ./kubernetes/apps/media/qui/app
  targetNamespace: *namespace
  components:
    - ../../../../components/volsync
    - ../../../../components/nfs-scaler
  dependsOn:
    - name: rook-ceph-cluster
      namespace: rook-ceph
  wait: false
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  interval: 1h
  retryInterval: 2m
  postBuild:
    substitute:
      APP: *app
      VOLSYNC_CAPACITY: 5Gi
```

- [ ] **Step 3: Check the file renders as YAML**

Run:

```bash
mise exec -- yq '.kind' kubernetes/apps/media/qui/ks.yaml
```

Expected output:

```text
Kustomization
```

---

### Task 2: Add the qui app manifests

**Files:**
- Create: `kubernetes/apps/media/qui/app/kustomization.yaml`
- Create: `kubernetes/apps/media/qui/app/helmrelease.yaml`

- [ ] **Step 1: Add the app Kustomization**

Create `kubernetes/apps/media/qui/app/kustomization.yaml` with:

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
```

- [ ] **Step 2: Add the HelmRelease**

Create `kubernetes/apps/media/qui/app/helmrelease.yaml` with:

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app qui
spec:
  chartRef:
    kind: OCIRepository
    name: app-template
  interval: 1h
  valuesFrom:
    - kind: ConfigMap
      name: affinity-control-1
  values:
    controllers:
      qui:
        annotations:
          reloader.stakater.com/auto: "true"
        containers:
          app:
            image:
              repository: ghcr.io/autobrr/qui
              tag: v1.19.0@sha256:baa07db5326f75f8c2246703603cbe2132476c8ad0ab31c976a960cb4c4731f5
            env:
              QUI__AUTH_DISABLED: true
              QUI__I_ACKNOWLEDGE_THIS_IS_A_BAD_IDEA: true
              QUI__AUTH_DISABLED_ALLOWED_CIDRS: ${LAN_CIDR},${POD_CIDR}
              QUI__HOST: 0.0.0.0
              QUI__PORT: &port 7476
              QUI__METRICS_ENABLED: true
              QUI__METRICS_HOST: 0.0.0.0
              QUI__METRICS_PORT: &metricsPort 8080
              TZ: ${TIMEZONE}
            probes:
              liveness:
                enabled: true
                spec:
                  periodSeconds: 30
                  timeoutSeconds: 5
                  failureThreshold: 5
              readiness:
                enabled: true
                custom: true
                spec:
                  httpGet:
                    path: /health
                    port: *port
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 5
              startup:
                enabled: true
                spec:
                  failureThreshold: 30
                  periodSeconds: 10
            resources:
              requests:
                cpu: 10m
                memory: 128Mi
              limits:
                memory: 2Gi
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities: { drop: ["ALL"] }
    defaultPodOptions:
      enableServiceLinks: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile: { type: RuntimeDefault }
    service:
      app:
        ports:
          http:
            port: *port
          metrics:
            port: *metricsPort
    serviceMonitor:
      app:
        endpoints:
          - port: metrics
    route:
      app:
        hostnames:
          - "{{ .Release.Name }}.${SECRET_DOMAIN}"
        parentRefs:
          - name: envoy-internal
            namespace: network
        rules:
          - backendRefs:
              - identifier: app
                port: *port
    persistence:
      config:
        existingClaim: *app
        globalMounts:
          - path: /config

      media:
        type: nfs
        server: ${TRUENAS_IP}
        path: /mnt/BIGHDDZ1/Media
        globalMounts:
          - path: /media
            readOnly: true

      tmp:
        type: emptyDir
```

Auth is disabled by explicit user instruction, but access is limited to `${LAN_CIDR},${POD_CIDR}` while the route remains internal-only through `envoy-internal`. If the route ever moves to an external gateway, enable qui auth/OIDC before exposing it.

- [ ] **Step 3: Check the app files render as YAML**

Run:

```bash
mise exec -- yq '.kind' kubernetes/apps/media/qui/app/kustomization.yaml && mise exec -- yq '.kind' kubernetes/apps/media/qui/app/helmrelease.yaml
```

Expected output:

```text
Kustomization
HelmRelease
```

---

### Task 3: Register qui in the media namespace

**Files:**
- Modify: `kubernetes/apps/media/kustomization.yaml`

- [ ] **Step 1: Add qui to the media resource list**

Modify `kubernetes/apps/media/kustomization.yaml` so the `resources` list includes `./qui/ks.yaml` after qBitrr and before Radarr:

```yaml
resources:
  - ./bazarr/ks.yaml
  - ./brrpolice/ks.yaml
  - ./cross-seed/ks.yaml
  - ./deduparr/ks.yaml
  - ./fileflows/ks.yaml
  - ./flaresolverr/ks.yaml
  - ./jellyfin/ks.yaml
  - ./seerr/ks.yaml
  - ./jellystat/ks.yaml
  - ./prowlarr/ks.yaml
  - ./qbittorrent/ks.yaml
  - ./qbittorrent/tools/qbitrr/ks.yaml
  - ./qui/ks.yaml
  - ./radarr/ks.yaml
  - ./recyclarr/ks.yaml
  - ./sonarr/ks.yaml
  - ./unpackerr/ks.yaml
  - ./wizarr/ks.yaml
```

- [ ] **Step 2: Confirm qui is registered once**

Run:

```bash
mise exec -- yq '.resources[]' kubernetes/apps/media/kustomization.yaml | rg '^\.\/qui\/ks\.yaml$'
```

Expected output:

```text
./qui/ks.yaml
```

---

### Task 4: Validate the initial qui deployment manifests

**Files:**
- Validate: `kubernetes/apps/media/qui/ks.yaml`
- Validate: `kubernetes/apps/media/qui/app/kustomization.yaml`
- Validate: `kubernetes/apps/media/qui/app/helmrelease.yaml`
- Validate: `kubernetes/apps/media/kustomization.yaml`

- [ ] **Step 1: Build the media Kustomization locally**

Run:

```bash
mise exec -- kustomize build kubernetes/apps/media >/tmp/qui-media-render.yaml
```

Expected: command exits 0.

- [ ] **Step 2: Confirm the rendered output contains qui resources**

Run:

```bash
rg -n 'name: qui|path: ./kubernetes/apps/media/qui/app|ghcr.io/autobrr/qui' /tmp/qui-media-render.yaml
```

Expected output includes these three matches:

```text
name: qui
path: ./kubernetes/apps/media/qui/app
ghcr.io/autobrr/qui
```

- [ ] **Step 3: Run full Kubernetes schema validation**

Run:

```bash
mise exec -- kubeconform -strict kubernetes/
```

Expected: command exits 0.

- [ ] **Step 4: Review the diff before any commit**

Run:

```bash
git diff -- kubernetes/apps/media/qui kubernetes/apps/media/kustomization.yaml docs/superpowers/specs/2026-05-24-qui-consolidation-design.md docs/superpowers/plans/2026-05-24-qui-consolidation.md
```

Expected: diff only contains the qui manifests, media kustomization registration, and docs changes.

- [ ] **Step 5: Get the qui manifests into the Git source before any live reconcile**

Flux reconciles the Git source, not local uncommitted files. Before Task 5, ask for approval to commit and publish the qui deployment changes. If approved, stage only these files:

```bash
git add \
  kubernetes/apps/media/qui/ks.yaml \
  kubernetes/apps/media/qui/app/kustomization.yaml \
  kubernetes/apps/media/qui/app/helmrelease.yaml \
  kubernetes/apps/media/kustomization.yaml \
  docs/superpowers/specs/2026-05-24-qui-consolidation-design.md \
  docs/superpowers/plans/2026-05-24-qui-consolidation.md
git commit -m "feat(media): add qui qBittorrent control plane"
```

Ask separately before pushing or opening a PR. Do not continue to live reconciliation until the watched Git branch contains the qui changes.

---

### Task 5: Deploy and configure qui manually

**Files:**
- No Git changes in this task unless post-deploy findings require manifest fixes.

- [ ] **Step 1: Ask for live-cluster apply approval**

Ask the user before running any reconcile command because live cluster changes require confirmation.

Use this exact question:

```text
Ready to reconcile qui to the live cluster and create a temporary healthcheck pod in namespace media. Do you approve these live-cluster operations?
```

- [ ] **Step 2: Reconcile the source, parent app Kustomization, and qui after approval**

Run only after approval:

```bash
mise exec -- flux reconcile source git flux-system -n flux-system && mise exec -- flux reconcile kustomization cluster-apps -n flux-system && mise exec -- flux reconcile kustomization qui -n media
```

Expected: both commands exit 0.

- [ ] **Step 3: Check qui rollout status**

Run:

```bash
mise exec -- kubectl -n media rollout status deployment/qui --timeout=5m
```

Expected output includes:

```text
deployment "qui" successfully rolled out
```

- [ ] **Step 4: Check qui health endpoint through the service**

Run:

```bash
mise exec -- kubectl -n media run qui-healthcheck --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- curl -fsS http://qui.media.svc.cluster.local/health
```

Expected: command exits 0.

- [ ] **Step 5: Configure qBittorrent in the qui UI**

Open `https://qui.${SECRET_DOMAIN}` and add the existing qBittorrent instance:

```text
Name: qbittorrent
URL: http://qbittorrent.media.svc.cluster.local
Username: existing qBittorrent username
Password: existing qBittorrent password
Filesystem path: /media
```

Expected: qui lists torrents, categories, tags, trackers, and save paths from qBittorrent.

- [ ] **Step 6: Create a non-invasive qBittorrent metadata backup in qui**

In the qui UI, create a qBittorrent metadata backup and preview restore contents without restoring.

Expected: backup preview includes torrents, tags, categories, save paths, and cached torrent blobs.

---

### Task 6: Remove qbit_manage GitOps resources

**Files:**
- Modify: `kubernetes/apps/media/qbittorrent/ks.yaml`
- Delete: `kubernetes/apps/media/qbittorrent/tools/qbitmanage/config/config.yaml`
- Delete: `kubernetes/apps/media/qbittorrent/tools/qbitmanage/helmrelease.yaml`
- Delete: `kubernetes/apps/media/qbittorrent/tools/qbitmanage/kustomization.yaml`

- [ ] **Step 1: Remove the qbitmanage Flux Kustomization**

Delete the second document from `kubernetes/apps/media/qbittorrent/ks.yaml`, the one whose metadata name is `qbitmanage`. The file must contain only the qBittorrent Kustomization document.

- [ ] **Step 2: Delete qbitmanage manifests**

Delete the whole `kubernetes/apps/media/qbittorrent/tools/qbitmanage/` directory.

- [ ] **Step 3: Confirm qbitmanage is no longer referenced in media manifests**

Run:

```bash
rg -n 'qbitmanage|qbit_manage' kubernetes/apps/media || true
```

Expected: no output.

- [ ] **Step 4: Validate after qbitmanage removal**

Run:

```bash
mise exec -- kubeconform -strict kubernetes/
```

Expected: command exits 0. If `mise` is unavailable, record that limitation in the PR and run fallback checks (`git diff --check`, YAML parse/assertions, and stale-reference grep).

- [ ] **Step 5: Ask before pruning live qbitmanage resources**

Do not reconcile live resources until the removal is committed, pushed, and merged. Then ask:

```text
qbitmanage is removed from Git. Do you approve reconciling Flux so qbitmanage live resources are pruned?
```

Run only after approval:

```bash
mise exec -- flux reconcile source git flux-system -n flux-system && mise exec -- flux reconcile kustomization cluster-apps -n flux-system
```

Expected: command exits 0 and qbitmanage is pruned by Flux.

- [ ] **Step 6: Verify qbitmanage is gone after live prune**

Run:

```bash
mise exec -- kubectl -n media get helmrelease,deploy,pod -l app.kubernetes.io/name=qbitmanage
```

Expected: no qbitmanage resources remain, or Kubernetes reports no resources found.

---

### Task 7: Recreate qbit_manage behavior in qui after deployment

**Files:**
- No Git changes in this task.

- [ ] **Step 1: Recreate metadata rules in qui**

In the qui UI, create automations matching the previous qbit_manage metadata behavior:

```text
Tracker tags:
- blutopia -> Blutopia, qBitrr-allowed_seeding
- digitalcore or prxy.digitalcore -> DigitalCore, qBitrr-allowed_seeding
- milkie -> Milkie
- myanonamouse -> MaM
- torrentleech or tleechreload -> TorrentLeech, qBitrr-allowed_seeding
- iptorrents -> IPTorrents, qBitrr-allowed_seeding
- alpharatio -> AlphaRatio, qBitrr-allowed_seeding
- hdspace or hd-space -> HDSpace, qBitrr-allowed_seeding
- fearnopeer -> FearNoPeer, qBitrr-allowed_seeding
- thegeeks or the-geeks -> TheGeeks
- upload.cx -> Upload.cx, qBitrr-allowed_seeding
- localhost.stackoverflow.tech -> StackOverflow, qBitrr-allowed_seeding
- unmatched trackers -> public

Category rules:
- /media/Downloads/radarr/ -> radarr
- /media/Downloads/tv-sonarr/ -> tv-sonarr
- /media/Downloads/manual/ -> manual
- /media/Downloads/ -> Downloads

Hardlink tags:
- radarr and tv-sonarr torrents without hardlinks -> noHL
```

Expected: qui can preview or show matching torrents for each rule before destructive actions are enabled.

- [ ] **Step 2: Create destructive qui automations disabled or preview-only**

In qui, create automations matching the previous qbit_manage policy, but keep them disabled or preview-only:

```text
Unregistered cleanup:
- Condition: torrent is unregistered
- Action: delete torrent according to the old qbit_manage removal policy

Public tracker share limit:
- Condition: tag includes public
- Ratio limit: 1.0
- Seeding time limit: 86400 seconds
- Cleanup: enabled

Private tracker seeding:
- Condition: tag includes AlphaRatio, Blutopia, DigitalCore, FearNoPeer, TheGeeks, IPTorrents, StackOverflow, TorrentLeech, or Upload.cx
- Ratio limit: unlimited
- Seeding time limit: unlimited
- Cleanup: disabled
```

Expected: qui shows affected torrents before actions are enabled, and the affected set matches expectations. Do not enable destructive qui actions yet.

- [ ] **Step 3: Enable destructive qui automations after review**

Only after qbitmanage removal has been committed, merged, reconciled, and pruned live, enable the destructive qui automations created in Step 2.

Expected: qui is the only owner of unregistered cleanup and share-limit cleanup.

- [ ] **Step 4: Accept orphan/recyclebin cleanup removal**

Do not preserve qbit_manage solely for orphan/recyclebin filesystem cleanup. This consolidation intentionally removes that behavior. Reintroduce it later only if there is a concrete need, preferably as a small purpose-built CronJob.

---

### Task 8: Leave qBitrr and cross-seed untouched, then evaluate separately

**Files:**
- No changes in this task.

- [ ] **Step 1: Confirm qBitrr remains registered**

Run:

```bash
mise exec -- yq '.resources[]' kubernetes/apps/media/kustomization.yaml | rg '^\.\/qbittorrent\/tools\/qbitrr\/ks\.yaml$'
```

Expected output:

```text
./qbittorrent/tools/qbitrr/ks.yaml
```

- [ ] **Step 2: Confirm cross-seed remains registered**

Run:

```bash
mise exec -- yq '.resources[]' kubernetes/apps/media/kustomization.yaml | rg '^\.\/cross-seed\/ks\.yaml$'
```

Expected output:

```text
./cross-seed/ks.yaml
```

- [ ] **Step 3: Create a future evaluation note**

Record this note in the PR body or implementation summary:

```text
Follow-up: evaluate qui cross-seed against standalone cross-seed after qui has stable qBittorrent visibility. Do not remove standalone cross-seed until qui proves equivalent Prowlarr/Sonarr/Radarr matching, hardlink behavior, partial matching, season-pack behavior, logs, and retry handling.
```

---

### Task 9: Final verification and review

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run full schema validation**

Run:

```bash
mise exec -- kubeconform -strict kubernetes/
```

Expected: command exits 0.

- [ ] **Step 2: Check for plaintext secrets in qui manifests**

Run:

```bash
rg -n 'password|secret|token|api[_-]?key' kubernetes/apps/media/qui || true
```

Expected: no output, because the initial qui deployment uses auth-disabled internal access and does not store qBittorrent credentials in Git.

- [ ] **Step 3: Check all expected files are tracked in the diff**

Run:

```bash
git status --short
```

Expected output includes the new qui files, docs, media registration, qbitmanage Kustomization removal, and qbitmanage file deletions.

- [ ] **Step 4: Run a final diff review**

Run:

```bash
git diff -- kubernetes/apps/media docs/superpowers
```

Expected: diff matches the chosen implementation phase and contains no unrelated changes.

- [ ] **Step 5: Commit only after explicit approval**

Ask the user for commit approval. If approved, run:

```bash
git add \
  kubernetes/apps/media/qui/ks.yaml \
  kubernetes/apps/media/qui/app/kustomization.yaml \
  kubernetes/apps/media/qui/app/helmrelease.yaml \
  kubernetes/apps/media/kustomization.yaml \
  kubernetes/apps/media/qbittorrent/ks.yaml \
  kubernetes/apps/media/qbittorrent/tools/qbitmanage/config/config.yaml \
  kubernetes/apps/media/qbittorrent/tools/qbitmanage/helmrelease.yaml \
  kubernetes/apps/media/qbittorrent/tools/qbitmanage/kustomization.yaml \
  docs/superpowers/specs/2026-05-24-qui-consolidation-design.md \
  docs/superpowers/plans/2026-05-24-qui-consolidation.md
git commit -m "feat(media): add qui qBittorrent control plane"
```

Expected: commit succeeds and includes only intended files.
