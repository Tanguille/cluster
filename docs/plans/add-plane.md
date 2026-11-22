# Add Plane Implementation Plan

**Created:** 2026-11-22
**Status:** In Progress (repo files implemented 2026-11-22 on one branch; live steps and owner steps open, see Implementation Status). Automated review: 0 CRIT; Chunk 0 and Decisions 7, 8 and 9 were added after it, then checked by a second read-only review whose points are folded in.
**Complexity:** High
**Estimated Chunks:** 7 (Chunk 0 added 2026-11-22 after the owner chose Ceph RGW)
**Branch:** `feat/add-plane` (from `origin/main`), one draft PR for everything (owner, 2026-11-22; the three-branch split below is superseded)
**Worktree:** `.claude/worktrees/feat-add-plane` (session claim in `~/.claude/sessions.md`, 2026-11-22 19:00)

---

## Implementation Status (2026-11-22)

Repo files for Chunks 0, 1, 2 and 5 are written and committed on `feat/add-plane`. Not pushed, no PR opened, nothing applied to the cluster.

Differences from the chunk text below:
- One branch and one PR instead of three. The MCPServer therefore merges before its token exists: the owner adds `PLANE_API_KEY` to `toolhive-secrets` before merge (a placeholder is enough), or the `plane` MCP pod sits in `CreateContainerConfigError` until Chunk 4 is done.
- No `rgw-ingress-allowlist`: RGW is on the host network (Chunk 0 step 2). Gateway port `8080`.
- `install.remediation.retries` not set: the helm-controller already runs with `DefaultToRetryOnFailure`.
- Hardening applied: `seccompProfile: RuntimeDefault` on every pod; `allowPrivilegeEscalation: false` and `drop: [ALL]` on all Plane containers except rabbitmq (escalation off only). `runAsNonRoot` is not set anywhere: it cannot be tested without the cluster. All of it is rendered, none of it has run.
- `PLANE_WORKSPACE_SLUG: home` (OQ-6 default).

Post-merge checklist, every live step with the owner's OK:
1. Chunk 0 verification (object store Ready, RGW log clean, `HEALTH_OK`, pool autoscale status, route check with `403` + `x-amz-request-id`, reachability from a `default` pod, presigned POST with a file over 1 MiB).
2. Chunk 1 step 5: Dragonfly db 13 is empty (`dbsize`), ideally before merge.
3. Chunk 2 step 8: `django_redis` `clear()` is `FLUSHDB` in the image.
4. Chunk 3 verification, including db 0 surviving an api restart and the Renovate dashboard listing `plane-ce`.
5. Chunk 4 by hand: instance admin, mail settings, workspace `home`, agent user, token, `PLANE_API_KEY` into `toolhive-secrets`.
6. Chunk 5 verification through the vMCP.
7. Chunk 6 after 7 days: requests from measured usage, then fold this plan into `docs/` and remove it.

---

## Overview

Deploy Plane CE (chart `plane-ce` 1.9.1, app v1.5.0) in namespace `default` behind an internal-only
HTTPRoute, on the existing CNPG and Dragonfly, with the chart's bundled RabbitMQ and uploads on a new Ceph RGW object store. Then expose it to
agents through a ToolHive `MCPServer`. Originally three PRs: RGW (Chunk 0), the app (Chunks 1 to 3), then the MCP server
(Chunk 5) once the owner has created the agent user and its token by hand (Chunk 4).

Nothing here is implemented yet. No push, no live apply and no SOPS edit without the owner's OK.

---

## User Stories

### US-1: Use Plane on the LAN
**As a** cluster owner
**I want to** open `https://plane.${SECRET_DOMAIN}` on the LAN
**So that** I have a self-hosted issue tracker

#### Acceptance Criteria
- [ ] Login, create a work item, upload an attachment, edit a page with live sync (`/live/` websocket)
- [ ] Not reachable through `envoy-external`

### US-2: Agents manage Plane through ToolHive
**As an** agent on the unified vMCP
**I want to** call Plane tools
**So that** I can file and update work items as a non-admin user

#### Acceptance Criteria
- [ ] `find_tool` returns Plane tools; `project(action="list")` succeeds (use the name the vMCP exposes, it may carry a `plane_` prefix)
- [ ] An admin-only action fails server-side (token belongs to a Member, not an Admin)

---

## Technical Analysis

### Findings that change the brief

Each one is verified; the source is named.

1. **Cloudflare R2 cannot serve Plane uploads.** Plane uploads with a presigned **POST**
   (`apps/api/plane/settings/storage.py` at `v1.5.0`: `s3_client.generate_presigned_post`). R2 docs,
   <https://developers.cloudflare.com/r2/api/s3/presigned-urls/>: "`POST` (multipart form uploads
   via HTML forms) is not currently supported." See Decision 3.
2. **The browser talks to the S3 endpoint directly** (presigned POST and GET), so the endpoint must
   be reachable from LAN browsers with CORS for `https://plane.${SECRET_DOMAIN}`, not only from pods.
3. **No Ceph RGW exists yet**: `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml:233` has
   `cephObjectStores: []`. The owner chose RGW on 2026-11-22; Chunk 0 enables it.
4. **Plane flushes its Redis database on every api start.** `bin/docker-entrypoint-api.sh` runs
   `python manage.py clear_cache`, which calls `cache.clear()`; with the `django_redis` backend
   (`settings/common.py` `CACHES`) that is `FLUSHDB`. With no db index in `REDIS_URL` this is db 0,
   which holds the vMCP sessions (`virtualmcpservers.yaml` `keyPrefix: thv:vmcp:unified:session:`)
   and every client that sets no index. **A dedicated db index is mandatory.** Chunk 2 re-checks the
   `FLUSHDB` claim inside the image before first start.
5. **The MCP server's `http` transport ignores `PLANE_API_KEY`.** README at `v0.3.6`: env keys are
   "Required for: stdio"; "The remote transports carry credentials in the connection ... and need
   none of these" (`Authorization` and `X-Workspace-slug` headers per request). ToolHive does not
   inject those headers, so the server runs **`transport: stdio`, `args: ["stdio"]`**, like
   `mealie.yaml` and `ghostfolio.yaml`. Port 8211 is not used.
6. **The chart has no securityContext, pod-label or env knobs** (`templates/workloads/*.yaml`), its
   Services are headless unless `assign_cluster_ip: true`, the ServiceAccount automounts its token,
   and an empty `storageClass` falls back to the cluster default class (set `ceph-block` explicitly). Labels
   and hardening go through Flux `postRenderers`, as in
   `kubernetes/apps/default/nextcloud/app/helmrelease.yaml:25`.
7. **Flux substitutes after SOPS decryption** (kustomize-controller
   `internal/controller/kustomization_controller.go`: `dec.DecryptResource` then
   `generator.SubstituteVariables`, per resource). So `${DRAGONFLY_PASSWORD}` inside a SOPS
   `stringData` value is substituted. Every other value in those secrets must be free of `$`.
8. **The CNPG cluster `postgres16` runs PostgreSQL 18.7** (`cluster/cluster.yaml:10`); Plane's chart
   ships 15.7. Untested combination; the owner accepted it on 2026-11-22 with the migrator Job as the gate.
9. **The Celery worker starts with no `--concurrency`** (`bin/docker-entrypoint-worker.sh`), so it
   forks one process per node core. Checked in Chunk 3.

### Data/Schema Changes

- New CNPG role `plane` and database `plane` (owner `plane`, no extensions). No schema doc to update.
- Dragonfly db index **13** (11 = searxng, 12 = tracearr, per `grep -rnE '6379/[0-9]+' kubernetes`).

### New Files
| File | Purpose |
|------|---------|
| `kubernetes/apps/database/cloudnative-pg/cluster/roles/plane.sops.yaml` | `plane-db` basic-auth Secret (ns `database`, label `cnpg.io/reload: "true"`), same shape as `roles/mealie.sops.yaml` |
| `kubernetes/apps/default/plane/ks.yaml` | Flux Kustomization, `dependsOn` `cloudnative-pg-databases` and `dragonfly-cluster` (check the exact name in `kubernetes/apps/database/dragonfly/ks.yaml`) |
| `kubernetes/apps/default/plane/app/kustomization.yaml` | resources list |
| `kubernetes/apps/default/plane/app/helmrepository.yaml` | `HelmRepository` `plane` -> `https://helm.plane.so/` (first one in the repo) |
| `kubernetes/apps/default/plane/app/helmrelease.yaml` | chart `plane-ce` 1.9.1, values, `postRenderers` |
| `kubernetes/apps/default/plane/app/httproute.yaml` | path-based route on `envoy-internal`, homepage annotations |
| `kubernetes/apps/default/plane/app/secret.sops.yaml` | Secrets `plane-app-env`, `plane-app-keys`, `plane-live-env`, `plane-rabbitmq` |
| `kubernetes/apps/default/plane/app/objectbucketclaim.yaml` | `ObjectBucketClaim` `plane-uploads` (Decision 7) |
| `kubernetes/apps/ai/toolhive/mcp/plane.yaml` | `MCPServer` `plane` (Chunk 5) |

### Modified Files
| File | Changes |
|------|---------|
| `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` | `spec.managed.roles`: add `plane` (`login: true`, `passwordSecret.name: plane-db`) |
| `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml` | add `roles/plane.sops.yaml` |
| `kubernetes/apps/database/cloudnative-pg/databases/resourceset.yaml` | input `- name: plane` (alphabetical, after `nextcloud`) |
| `kubernetes/apps/default/kustomization.yaml` | add `./plane/ks.yaml` |
| `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` | `cephObjectStores`: one store, storage class `ceph-bucket`, route (Chunk 0) |
| `.renovaterc.json5` | `plane-ce` packageRule, `automerge: false` |
| `kubernetes/apps/ai/toolhive/mcp/kustomization.yaml` | add `plane.yaml` (Chunk 5) |
| `kubernetes/apps/ai/toolhive/app/secret.sops.yaml` | **owner only**: add key `PLANE_API_KEY` (Chunk 4) |

### Routes (from the chart's `templates/ingress.yaml`)
| PathPrefix | Backend |
|------------|---------|
| `/` | `plane-web:3000` |
| `/api`, `/auth`, `/static` | `plane-api:8000` |
| `/live` | `plane-live:3000` (websocket) |
| `/spaces` | `plane-space:3000` |
| `/god-mode` | `plane-admin:3000` |
| `/plane-uploads` | `rook-ceph-rgw-ceph-objectstore.rook-ceph:80`, rendered by the rook chart's own `route` in namespace `rook-ceph` on the same hostname (Decision 8) |

### Conventions Checklist
- [ ] `git pull --ff-only && git status` in the worktree before each chunk (`AGENTS.md`)
- [ ] `.yaml` only, block style, terse comments, no pin comments, no em dashes
- [ ] Tools through `mise exec --` (`.agents/common-operations.md`)
- [ ] Versions and digests from upstream, never from memory
- [ ] Conventional Commit titles, no Claude attribution
- [ ] `.agents/skills/add-app-to-cluster/SKILL.md` read before Chunk 2

---

## Decisions & Trade-offs

### Decision 1: Postgres path
**Options:**
1. `pgbouncer-rw` (transaction mode), as mealie. Breaks Django server-side cursors and session state; Plane exposes no `DISABLE_SERVER_SIDE_CURSORS` switch.
2. `pgbouncer-session` (session mode), as nextcloud (`helmrelease.yaml:226`). Keeps failover handling, TLS and the pool ceiling (2 x 20 per db and user, `cluster/poolers.yaml`).
3. Direct `postgres16-rw`. No pooler ceiling against `max_connections: 150`.

**Chosen:** 2, `pgbouncer-session.database.svc.cluster.local:5432`.
**Rationale:** Django plus Celery needs session semantics; Plane holds few connections (1 gunicorn worker, worker, beat, migrator). URL carries `?sslmode=require` because the poolers set `client_tls_sslmode: require`. The existing gate `kubernetes/apps/database/networkpolicy.yaml:22` already allows 5432 to any pod labelled `egress.home.arpa/postgres: allow`.

### Decision 2: How the passwords reach `DATABASE_URL` and `REDIS_URL`
**Options:**
1. Literal Dragonfly password inside the SOPS secret. Needs `cluster-secrets` decrypted and duplicates the password.
2. `$(DRAGONFLY_PASSWORD)` env expansion as tracearr/searxng. The chart has no env knob; needs five more postRenderer patches.
3. `${DRAGONFLY_PASSWORD}` inside the SOPS `stringData`, substituted by Flux after decryption (finding 7). `kubernetes/components/dragonfly-auth/secret.yaml` uses the same variable in a plain Secret; no SOPS file in the repo is known to rely on it, so Chunk 3's "db 13 gained keys" check is the gate. It fails loudly (Redis AUTH errors), never silently.

**Chosen:** 3. `REDIS_URL: redis://:${DRAGONFLY_PASSWORD}@dragonfly.database.svc.cluster.local:6379/13`.
**Rationale:** one line, no decryption of any existing secret. The DB password is generated once and written into both new SOPS files (Chunk 1). Chart pods get `reloader.stakater.com/auto: "true"` through each component's `annotations` value (rendered on the Deployment metadata by `plane.labelsAndAnnotations`), because the chart's own checksum annotation only covers secrets it renders itself.

### Decision 3: Object storage for uploads
**Chosen (owner, 2026-11-22):** Ceph RGW, a `CephObjectStore` in the existing rook-ceph cluster.
R2 is out (finding 1). The bundled object store and TrueNAS S3 were considered and dropped.

**Pool sizing.** Three OSDs, one per host; the existing pools are replicated `size: 2`, `failureDomain: host` (`cluster/helmrelease.yaml:155-194`), and Ceph derives `min_size 1` from that.
- The chart's example data pool is erasure-coded 2+1. On three hosts that has `min_size 3`, so the pool stops serving I/O whenever one node is down (every Talos upgrade). Not used.
- Chosen: metadata and data pool both replicated `size: 2`, `failureDomain: host`, like the block pool. Uploads stay available through single-node maintenance; the known cost is the same as for every RBD volume: one copy during that window, so a second fault is data loss. The owner accepted no backup for uploads (2026-11-22).
- RGW creates about seven pools. With three OSDs the PG autoscaler must stay under `mon_max_pg_per_osd`; Chunk 0 checks `ceph osd pool autoscale-status` and `ceph health` for `TOO_MANY_PGS`.

**Ceph v21 notes in the repo.** The CephFS zero-read bug and the `readAffinity` kernel floor (`helmrelease.yaml:91-95`) concern the in-kernel client; RGW is a userspace librados client and is not affected. The mgr map-cache leak (`:77`) is independent of RGW. `security.cephx.allowedCiphers: ["aes256k"]` (`:50-56`) applies to the RGW daemon key Rook creates; whether RGW on v21.2.0 starts cleanly under it is unverified and is Chunk 0's first check.

### Decision 7: Bucket and credentials
**Options:**
1. `CephObjectStoreUser`: Rook writes the key Secret into `rook-ceph`. Plane runs in `default`, so the key would need a cross-namespace copy (no tool for that in the repo) or a SOPS copy made by hand.
2. `ObjectBucketClaim` in `default`: Rook creates the bucket, a user scoped to it, and a Secret plus ConfigMap named after the claim **in the claim's namespace**.

**Chosen:** 2. `ObjectBucketClaim` `plane-uploads` in `default`, `bucketName: plane-uploads`, `storageClassName: ceph-bucket`.
**Rationale:** the Secret `plane-uploads` (keys `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) lands where Plane runs, with no SOPS file and nothing copied. The chart reads it natively: `external_secrets.storage.secretName: plane-uploads`, `accessKeyIdKey: AWS_ACCESS_KEY_ID`, `secretAccessKeyKey: AWS_SECRET_ACCESS_KEY` (`plane.storageCredsEnv`, rendered on api, worker, beat-worker and the migrator Job). The remaining doc-store keys are not secret and come from chart values, so the `plane-doc-store` SOPS Secret is dropped. Storage class `reclaimPolicy: Retain`, so pruning the claim never deletes the bucket.

### Decision 8: How the browser reaches the bucket
Uploads are a presigned POST and downloads a presigned GET, both issued for the single `AWS_S3_ENDPOINT_URL` (`storage.py:64`, the `USE_MINIO=0` branch). The browser and the Plane pods therefore use the same URL, and SigV4 signs its host.

**Options:**
1. Own hostname (`rgw.${SECRET_DOMAIN}`; `s3.${SECRET_DOMAIN}` is taken by TrueNAS). Cross-origin, so the bucket needs a CORS rule. Rook has no declarative CORS field for a claim; it would be a one-off `aws s3api put-bucket-cors`, outside git. Exposes the whole S3 API on the LAN.
2. Same origin: `AWS_S3_ENDPOINT_URL: https://plane.${SECRET_DOMAIN}` and a route for `PathPrefix /plane-uploads` on that hostname to the RGW Service. boto3 uses path-style addressing for a custom endpoint, so every object URL is `https://plane.${SECRET_DOMAIN}/plane-uploads/<key>`.

**Chosen:** 2.
**Rationale:** no CORS at all, no second hostname, and only the one bucket path is routed. That is not a fence: RGW is on the host network, so any pod or LAN host can reach node port 8080 directly and no Cilium policy applies. The S3 credentials are the only gate, like the mgr dashboard's login. The route is the rook chart's own `cephObjectStores[].route` (`host.name: plane.${SECRET_DOMAIN}`, `host.path: /plane-uploads`, `pathType: PathPrefix`, `parentRefs: envoy-internal`), rendered in `rook-ceph` next to the dashboard route that already attaches to `envoy-internal`. That avoids a cross-namespace `backendRef` and the repo's first `ReferenceGrant`. Gateway API merges routes from two namespaces on one hostname and the longer prefix wins over `/`.

**Unverified, gated in Chunks 0 and 3:**
- RGW accepts a presigned POST under an unknown `Host` as a path-style request (expected: any host that is not `rgw_dns_name` is path-style).
- Plane pods (api, worker) reach `https://plane.${SECRET_DOMAIN}` from inside the cluster. `deny-lan-egress` denies `192.168.0.0/16`, and `ENVOY_INTERNAL_IP` is in it. The policy carries a separate `toEndpoints` deny for `envoy-internal-tls` pods, which suggests traffic to a gateway LB address is matched as its backend endpoint, not as a LAN CIDR; that is inference, not a test. If the check fails, the fallback is `egress.home.arpa/lan: allow` on api and worker only (approved by the owner on 2026-11-22 for exactly that case); it is added to Chunk 2's label patch and noted in the PR body.


### Decision 4: RabbitMQ PVC backup
**Chosen:** no kopiur backup.
**Rationale:** it is only the Celery broker; losing it drops in-flight background jobs (mail, webhooks, exports) and Plane re-declares its queues on start. The shared kopiur component also assumes one PVC named `${APP}` per Kustomization (`kubernetes/components/kopiur/backup/snapshotpolicy.yaml`), which does not fit `volumeClaimTemplates` names. Uploads now live in RGW, which kopiur (PVC-based) does not cover; the owner accepted that (2026-11-22).

### Decision 5: Chart source and Renovate
**Chosen:** `HelmRepository` + classic `chart.spec` (`chart: plane-ce`, `version: 1.9.1`, `sourceRef`), no `planeVersion` and no image overrides in values.
**Rationale:** no OCI chart exists. Renovate's `flux` manager resolves a HelmRelease through a HelmRepository in the same repo. The Plane images (`artifacts.plane.so/makeplane/plane-*:<planeVersion>`) and `rabbitmq:3.13.6-management-alpine` are chart defaults, so they move with the chart bump and need no Renovate rule. Digest pinning is not possible: one shared tag for seven images. Set every `pullPolicy: IfNotPresent` (chart default is `Always`).

### Decision 6: MCP tool filter
**Chosen:** none at first, like `mealie.yaml` and `karakeep.yaml`.
**Rationale:** the server has no include/exclude env. An include-filter does exist in the repo (`MCPToolConfig.spec.toolsFilter` in `truenas.yaml`, `github.yaml`, `flux-operator.yaml`, `opnsense/mcpserver.yaml`), wired only through `virtualmcpservers.yaml` `config.aggregation.tools` because a proxy-level `toolConfigRef` strips `resultType` (stacklok/toolhive#6684). It filters whole tools, and Plane's 30 tools each take an `action`, so it cannot block `delete`. Access control is the agent user's role. Confirmed by the owner on 2026-11-22.

### Decision 9: Outgoing mail
**Chosen (owner, 2026-11-22):** the existing relay `smtp-relay.network.svc.cluster.local` (`kubernetes/apps/network/smtp-relay/app/helmrelease.yaml`, Postfix to `smtp.gmail.com:587`).

- **Relay facts:** Service port **25**. No client auth: `POSTFIX_mynetworks: "${POD_CIDR}"` relays for any pod address. No TLS configured on the listening side, so clients use plain SMTP; the relay does TLS to Gmail itself (`smtp_tls_security_level: secure`). Message limit 50 MiB.
- **Network:** nothing to add. Egress from `default` is default-allow; `deny-lan-egress` only denies `172.16/12` and `192.168/16`, and the relay is in the pod and service ranges. Namespace `network` has no CiliumNetworkPolicy (`kubernetes/apps/network/kustomization.yaml` lists none), so ingress to the relay is open. No label gate exists for it. Nextcloud is not a usable precedent: its `SMTP_HOST` is a key of its SOPS secret and its values say `domain: gmail.com`, `secure: starttls`, so whether it uses the relay cannot be read from the repo.
- **How Plane takes SMTP config:** from the database, set in god-mode, not from env. `SKIP_ENV_VAR` defaults to `"1"` (`settings/common.py:401`), and with it `get_email_configuration()` (`license/utils/instance_value.py:42`) reads the `InstanceConfiguration` rows through `get_configuration_value()` (`:17-40`). Env `EMAIL_*` values only seed those rows when `configure_instance` first creates them (`get_or_create`, written only `if created`), and the chart has no email values at all. So the settings are entered **by hand in god-mode** (Chunk 4) and live only in the `plane` database, covered by the CNPG backups. Mail is sent by the api and worker pods.
- **Values to enter:** host `smtp-relay.network.svc.cluster.local`, port `25`, no username, no password, encryption none (TLS and SSL both off), sender address = the Gmail account the relay logs in as (Gmail rewrites any other From). The address is the owner's and stays out of git.
- **Not done:** seeding through `EMAIL_*` keys in `plane-app-env`. It would work once, on first start only, and then silently stop being the source of truth.

---

## Dependencies

### Prerequisites
- [ ] Chunk 0 merged and healthy before Chunk 3 merges (the app Kustomization also `dependsOn` `rook-ceph-cluster`)
- [ ] Owner OK to create new SOPS files (Chunk 1), to push (Chunk 3) and to merge
- [ ] Dragonfly db 13 is empty: `redis-cli -n 13 dbsize` returns 0 (read-only check, Chunk 1)

### External Dependencies
- Chart `https://helm.plane.so/` `plane-ce` 1.9.1
- `.renovaterc.json5`: patch updates auto-merge after 3 days and the app images follow the chart, so a chart patch can run migrations unattended. Add a packageRule `matchPackageNames: ["plane-ce"]`, `automerge: false` (Chunk 2 write set).
- Image `docker.io/makeplane/plane-mcp-server:v0.3.6@sha256:435df7ccd08aeee1f42222157831a4b8a6bc044de333f501949d9c364a8c7f76` (Docker Hub tags API, 2026-11-22; re-check at Chunk 5)

---

## Execution Plan

### Progress Tracker

#### Wave 0 (sequential): Ceph RGW
- [x] Chunk 0: enable the object store, own PR (repo files; live checks open)

#### Wave 1 (sequential): Database and secrets
- [x] Chunk 1: CNPG role, database, new SOPS secrets (repo files; live checks open)

#### Wave 2 (sequential): App manifests
- [x] Chunk 2: Plane HelmRelease, route, hardening (depends on Chunks 0, 1) (repo files; live checks open)

#### Wave 3 (sequential): Ship PR 1
- [ ] Chunk 3: validate, PR, merge, verify live (depends on Chunk 2)

#### Wave 4 (sequential): Owner bootstrap
- [ ] Chunk 4: first login, agent user, token (owner, by hand; depends on Chunk 3)

#### Wave 5 (sequential): MCP server
- [x] Chunk 5: ToolHive MCPServer (repo files, same PR; needs `PLANE_API_KEY` from Chunk 4 before it can start)

#### Wave 6 (sequential): Sizing and docs
- [ ] Chunk 6: requests from 7 days of data, final docs pass (depends on Chunk 5)

### Wave Conflict Matrix
| Chunk | Write Set | Dependencies | Wave |
|-------|-----------|--------------|------|
| 0 | `rook-ceph/rook-ceph/cluster/helmrelease.yaml` | None | 0 |
| 1 | `cloudnative-pg/cluster/{cluster.yaml,kustomization.yaml,roles/plane.sops.yaml}`, `cloudnative-pg/databases/resourceset.yaml`, `default/plane/app/secret.sops.yaml` | None | 1 |
| 2 | `default/plane/ks.yaml`, `default/plane/app/{kustomization,helmrepository,helmrelease,httproute,objectbucketclaim}.yaml`, `default/kustomization.yaml`, `.renovaterc.json5` | Chunks 0, 1 | 2 |
| 3 | none (fixes go back to the owning chunk's files) | Chunk 2 | 3 |
| 4 | `ai/toolhive/app/secret.sops.yaml` (owner) | Chunk 3 | 4 |
| 5 | `ai/toolhive/mcp/{plane.yaml,kustomization.yaml}` | Chunk 4 | 5 |
| 6 | `default/plane/app/helmrelease.yaml`, `ai/toolhive/mcp/plane.yaml`, docs | Chunk 5 | 6 |

---

### Chunk 0: Ceph RGW object store
**Status:** Not Started
**Wave:** 0
**Estimated Context:** ~40k tokens
**Complexity:** Medium (touches the shared storage HelmRelease)

#### Scope
- `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` - `cephObjectStores`

#### Prerequisites
- None (Wave 0). Branch `feat/ceph-rgw` from updated `origin/main`, worktree `.claude/worktrees/feat-ceph-rgw` (copy `.mcp.json .env CLAUDE.local.md .vscode/ .claude/`), line in `~/.claude/sessions.md`. Read the live claims first: a bench window on the LLM stack was open on 2026-11-22.
- `ceph status` is `HEALTH_OK` and no node maintenance is planned for the day (read-only check).

#### Detailed Steps

1. **Values**, field names from the chart's `values.yaml` at `v1.21.2` (the repo's pinned tag):
   ```yaml
   cephObjectStores:
     - name: ceph-objectstore
       spec:
         metadataPool:
           failureDomain: host
           replicated:
             size: 2
         dataPool:
           failureDomain: host
           replicated:
             size: 2
           parameters:
             compression_mode: aggressive
             compression_algorithm: zstd
         preservePoolsOnDelete: true
         gateway:
           port: 8080
           priorityClassName: homelab-optional
           instances: 1
           resources:
             requests:
               cpu: 50m
               memory: 256Mi
             limits:
               memory: 1Gi
       storageClass:
         enabled: true
         name: ceph-bucket
         reclaimPolicy: Retain
         parameters:
           region: us-east-1
       route:
         enabled: true
         annotations:
           gatus.home-operations.com/enabled: "false"
         host:
           name: plane.${SECRET_DOMAIN}
           path: /plane-uploads
           pathType: PathPrefix
         parentRefs:
           - name: envoy-internal
             namespace: network
   ```
   - Requests are provisional round values (the chart example asks 1 CPU / 1Gi); Chunk 6 sizes them from data. One instance (owner, 2026-11-22).
   - `gateway.priorityClassName: homelab-optional` (owner, 2026-11-22; the chart example uses `system-cluster-critical`). Add it to the values above.
   - One-line comment on the route: it serves Plane's bucket path on Plane's hostname so uploads stay same-origin.
   - Before writing, render with `mise exec -- helm template` against the chart at `1.21.2` to confirm the `route` block produces an `HTTPRoute` with that hostname and path, and note the RGW Service name and port.
2. **No network policy.** RGW runs on the host network (`cephClusterSpec.network.provider: host`, `helmrelease.yaml:108`), where Cilium enforces no endpoint policy, so a CiliumNetworkPolicy on the RGW pod would do nothing. The S3 credentials are the only gate, as the dashboard login is for the mgr. For the same reason the gateway listens on `8080`, not the privileged `80`; the chart's route port follows `gateway.port` (rendered and checked).
3. `validate-pr.sh`; push, PR and merge each need the owner's OK.

#### Verification
- [ ] `mise exec -- flate test all -p .` and `validate-pr.sh` pass
- [ ] After merge: `CephObjectStore/ceph-objectstore` `Ready`; RGW pod Running with no cephx or cipher error in its log (Decision 3)
- [ ] `ceph status` `HEALTH_OK`; `ceph osd pool autoscale-status` shows the new pools at size 2 with no `TOO_MANY_PGS`; existing RBD and CephFS workloads unaffected (no new slow-op alerts in 30 min)
- [ ] `curl -si https://plane.${SECRET_DOMAIN}/plane-uploads/` from the LAN returns `403` with an `x-amz-request-id` header. Do not match on the body: envoy's `eep` wasm filter replaces 404 and 5xx bodies, so the S3 XML is not reliable. Nothing is served on `envoy-external`
- [ ] The same `curl` from a `default` pod without the `egress.home.arpa/lan` label succeeds (Decision 8). If it fails, record that in the plan: Chunk 2 then labels api and worker with `egress.home.arpa/lan: allow`
- [ ] Presigned POST works against RGW (live step, owner OK): with a throwaway claim in `rook-ceph`, generate a presigned POST for `https://plane.${SECRET_DOMAIN}` with boto3 and upload a file larger than 1 MiB with `curl -F`; download it back and compare; delete the claim and its bucket afterwards. If RGW rejects it, stop: Decision 8 option 1 is the fallback and needs the owner

#### Files Changed Summary
```
feat(rook-ceph): add rgw object store
kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml
```

#### Continuation Prompt
> Continue the Add Plane plan from `docs/plans/add-plane.md`.
> 1. Verify branch `feat/add-plane` and worktree `.claude/worktrees/feat-add-plane`, `git pull --ff-only && git status` (the branch was 3 commits behind `origin/main` on 2026-11-22)
> 2. Mark Chunk 0 complete; remove the `feat/ceph-rgw` worktree, branch and session claim
> 3. Proceed with Chunk 1: CNPG role, database, new SOPS secrets

---


### Chunk 1: CNPG role, database, new SOPS secrets
**Status:** Not Started
**Wave:** 1
**Estimated Context:** ~30k tokens
**Complexity:** Medium

#### Scope
- `kubernetes/apps/database/cloudnative-pg/cluster/roles/plane.sops.yaml` - new
- `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` - role entry
- `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml` - resource entry
- `kubernetes/apps/database/cloudnative-pg/databases/resourceset.yaml` - input
- `kubernetes/apps/default/plane/app/secret.sops.yaml` - new, four Secret documents

#### Prerequisites
- None (Wave 1). Owner OK for creating new SOPS files (encrypt only, no decryption; `.sops.yaml` rule `(bootstrap|kubernetes)/.*\.sops\.ya?ml`).

#### Detailed Steps

1. **Branch check**: `git -C <worktree> status -sb` shows `feat/add-plane`; `git pull --ff-only`.
2. **Role**: add to `cluster.yaml` `spec.managed.roles` (after `mealie`): `name: plane`, `login: true`, `passwordSecret.name: plane-db`. Add `roles/plane.sops.yaml` to `cluster/kustomization.yaml`.
3. **Database**: add `- name: plane` to `resourceset.yaml` inputs. No `extensions`.
4. **Secrets**, generated in one shell so no value is printed or stored outside the two files. All values hex (`openssl rand -hex 32`), so they are URL-safe and contain no `$`:
   - `roles/plane.sops.yaml`: `plane-db`, `type: kubernetes.io/basic-auth`, ns `database`, label `cnpg.io/reload: "true"`, `stringData.username: plane`, `stringData.password: <DB_PW>`.
   - `secret.sops.yaml`:
     - `plane-app-env`: `DATABASE_URL: postgresql://plane:<DB_PW>@pgbouncer-session.database.svc.cluster.local:5432/plane?sslmode=require`, `REDIS_URL: redis://:${DRAGONFLY_PASSWORD}@dragonfly.database.svc.cluster.local:6379/13`, `AMQP_URL: amqp://plane:<MQ_PW>@plane-rabbitmq.default.svc.cluster.local:5672/`
     - `plane-app-keys`: `SECRET_KEY`, `LIVE_SERVER_SECRET_KEY`. `SECRET_KEY` derives the key that encrypts instance configuration: never rotate it; add a one-line comment saying so.
     - `plane-live-env`: `REDIS_URL` (same value)
     - `plane-rabbitmq`: `RABBITMQ_DEFAULT_USER: plane`, `RABBITMQ_DEFAULT_PASS: <MQ_PW>`
   - Encrypt each with `mise exec -- sops --encrypt --in-place <file>`. The literal text `${DRAGONFLY_PASSWORD}` goes in before encryption.
5. **Db index check**: ask the owner first (it needs the Dragonfly password, a secret read). Then `kubectl exec -n database <dragonfly master pod> -- redis-cli -a <pw> -n 13 dbsize` must return 0.

#### Verification
- [ ] `grep -c 'ENC\[' <each new sops file>` is non-zero and `grep -n 'password: [^E]' ` finds no plaintext; `mise exec -- python3 scripts/secret-check.py --all` passes
- [ ] `mise exec -- flate test all -p .` passes (ResourceSet and cluster Kustomization still render)
- [ ] E2E is deferred to Chunk 3: Database `plane` Ready and role login work only after merge

#### Files Changed Summary
```
feat(cloudnative-pg): add plane role and database
kubernetes/apps/database/cloudnative-pg/cluster/{cluster.yaml,kustomization.yaml,roles/plane.sops.yaml}
kubernetes/apps/database/cloudnative-pg/databases/resourceset.yaml
kubernetes/apps/default/plane/app/secret.sops.yaml
```

#### Continuation Prompt
> Continue the Add Plane plan from `docs/plans/add-plane.md` in worktree `.claude/worktrees/feat-add-plane`.
> 1. Verify branch `feat/add-plane` and the worktree, `git pull --ff-only && git status`, `git log --oneline -5`
> 2. Mark Chunk 1 complete in the tracker
> 3. Proceed with Chunk 2: Plane HelmRelease, route, hardening

---

### Chunk 2: Plane HelmRelease, route, hardening
**Status:** Not Started
**Wave:** 2
**Estimated Context:** ~70k tokens
**Complexity:** High

#### Scope
- `kubernetes/apps/default/plane/ks.yaml`, `app/kustomization.yaml`, `app/helmrepository.yaml`, `app/helmrelease.yaml`, `app/httproute.yaml`, `app/objectbucketclaim.yaml`
- `.renovaterc.json5` - `plane-ce` packageRule, `automerge: false`
- `kubernetes/apps/default/kustomization.yaml`

#### Prerequisites
- Chunks 0 and 1. Chart at `/tmp/plane-chart/plane-ce` (re-download the tarball from the GitHub release if gone).

#### Detailed Steps

1. **`ks.yaml`**: copy `kubernetes/apps/default/mealie/ks.yaml`; drop the kopiur component and its substitutions; keep `dependsOn: cloudnative-pg-databases`; add `dragonfly-cluster` (namespace `database`) and `rook-ceph-cluster` (namespace `rook-ceph`).
2. **`helmrepository.yaml`**: `source.toolkit.fluxcd.io/v1` `HelmRepository` `plane`, `url: https://helm.plane.so/`, `interval: 1h`.
3. **`helmrelease.yaml`**: `timeout: 15m` (as nextcloud; Flux waits for 7 first pulls plus the migrator Job, default is 5m) and `install.remediation.retries: 2`. Values:
   - `ingress.enabled: false`, `ingress.appHost: plane.${SECRET_DOMAIN}`, `ssl.externalTermination: true` (makes `WEB_URL` and CORS `https://`, see `plane.tlsEnabled`)
   - `postgres.local_setup: false`, `redis.local_setup: false`, `rabbitmq.local_setup: true`, `minio.local_setup: false`
   - storage (Decisions 7 and 8): `env.docstore_bucket: plane-uploads`, `env.aws_s3_endpoint_url: https://plane.${SECRET_DOMAIN}`, `env.aws_region: us-east-1`, `env.aws_access_key` and `env.aws_secret_access_key` left empty; `external_secrets.storage.secretName: plane-uploads`, `accessKeyIdKey: AWS_ACCESS_KEY_ID`, `secretAccessKeyKey: AWS_SECRET_ACCESS_KEY`. The chart then renders `plane-doc-store-secrets` itself with `USE_MINIO: "0"` and no credentials (`templates/config-secrets/doc-store.yaml`)
   - `rabbitmq.storageClass: ceph-block`, `rabbitmq.volumeSize: 1Gi`
   - `env.requireExplicitSecrets: true`
   - `external_secrets`: `app_env_existingSecret: plane-app-env`, `app_keys_existingSecret: plane-app-keys`, `live_env_existingSecret: plane-live-env`, `rabbitmq_existingSecret: plane-rabbitmq`. `doc_store_existingSecret` stays empty. `pgdb_existingSecret` stays empty (only read by the local Postgres).
   - `pullPolicy: IfNotPresent` on web, space, admin, live, api, worker, beatworker. `assign_cluster_ip: true` on web, space, admin, live, api, rabbitmq (ClusterIP Services, what every other route in the repo targets; immutable later)
   - `api`, `worker`, `beatworker`, `live`: `annotations: reloader.stakater.com/auto: "true"`
   - resources: provisional round values, replaced in Chunk 6. web/space/admin `10m`/`32Mi` limit `128Mi`; api `50m`/`512Mi` limit `1Gi`; worker `50m`/`512Mi` limit `2Gi`; beatworker and live `10m`/`128Mi` limit `512Mi`. The chart always renders a CPU limit: `cpuLimit: "2"` on api and worker, chart default `500m` elsewhere. `env.api_key_rate_limit: 300/minute` (default `60/minute` throttles an agent).
4. **`postRenderers`** (kustomize patches, pattern from nextcloud):
   - `ServiceAccount plane-srv-account`: `automountServiceAccountToken: false`
   - Deployments `plane-(api|worker|beat-worker)-wl` and Jobs `plane-api-migrate-.*`: pod labels `egress.home.arpa/postgres: allow` and `egress.home.arpa/dragonfly: allow`
   - Deployment `plane-live-wl`: `egress.home.arpa/dragonfly: allow` only
   - all Deployments, StatefulSets, Jobs: container `securityContext` `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`; pod `seccompProfile.type: RuntimeDefault`
   - StatefulSet `plane-rabbitmq-wl`: requests `50m`/`256Mi`, limit `512Mi` (the template renders none)
   - Deployment `plane-worker-wl`: container command `["/bin/bash", "-c", "python manage.py wait_for_db && python manage.py wait_for_migrations && exec celery -A plane worker -l info --concurrency=2"]` (the three steps of `bin/docker-entrypoint-worker.sh` at `v1.5.0` plus the cap, finding 9)
   - Patch mechanics: JSON6902 on `containers/0` per target (container names differ per workload, so one strategic-merge patch cannot cover them). Escape `/` in label keys as `~1` (`egress.home.arpa~1postgres`). Flux substitutes inside the HelmRelease too: write a literal `$` as `$$`.
   - Confirm every target name against `helm template plane /tmp/plane-chart/plane-ce -f <values>`; a patch whose target matches nothing is silent.
5. **Non-root, staged.** The namespace enforces PSA `baseline` (`kubernetes/components/common/namespace.yaml`), which the stock chart passes, so root is not a blocker. Per image, from the Dockerfiles at `v1.5.0`:
   - backend (api, worker, beat, migrator): no `USER`, `/code` is `chmod -R 777`; try `runAsNonRoot` uid/gid 1000. No `readOnlyRootFilesystem` (`collectstatic` and logs write under `/code`).
   - web, space, admin: Caddy on 3000, no `USER`; non-root needs writable `XDG_DATA_HOME`/`XDG_CONFIG_HOME`. Leave root unless a quick test passes.
   - live: `node apps/live`, no `USER`; try uid 1000.
   - rabbitmq: the official entrypoint starts as root and drops; with `drop: [ALL]` it needs the image's own uid plus `fsGroup` instead. Look the uid up in the image, do not guess.
   - Rule: keep a hardening line only if the pod reaches Ready with it in Chunk 3; otherwise remove that line and note it in a one-line comment.
6. **`httproute.yaml`**: one `HTTPRoute` `plane`, `parentRefs: envoy-internal` in `network`, hostname `plane.${SECRET_DOMAIN}`, `PathPrefix` rules from the Routes table. Annotations: `gethomepage.dev/enabled: "true"`, `name: Plane`, `description: Project management`, `group: Utilities`, `icon: plane.png` (confirm the icon exists in dashboard-icons).
7. **`objectbucketclaim.yaml`**: `objectbucket.io/v1alpha1` `ObjectBucketClaim` `plane-uploads`, `spec.bucketName: plane-uploads`, `spec.storageClassName: ceph-bucket`. Rook writes Secret and ConfigMap `plane-uploads` into `default`. The api entrypoint's `create_bucket` does `head_bucket` first and skips creation when the bucket exists. The Secret is created by Rook, not by Flux: if the pods start before it exists they sit in `CreateContainerConfigError` and recover by themselves; `timeout: 15m` covers it.
8. **Before first start**, confirm finding 4 in the image: `django_redis/client/default.py` `clear()` calls `flushdb()`. If it deletes by prefix instead, db 13 is still kept.
9. **Network policy**: no new policy in `default`. `default-ingress-allowlist` (`kubernetes/apps/default/networkpolicy.yaml`) already admits `network` (envoy), `default` (intra-namespace: api to rabbitmq, live to api) and `ai` (MCP). Egress is default-allow; `deny-apiserver-egress` needs no opt-out. Uploads leave the pods towards `https://plane.${SECRET_DOMAIN}` (envoy-internal), which Chunk 0 proved reachable without the `egress.home.arpa/lan` label. RGW has no network fence (host network, Chunk 0 step 2).

#### Verification
- [ ] `mise exec -- flate test all -p .` passes and renders the Plane HelmRelease from the HelmRepository
- [ ] Rendered output with postRenderers applied (flate's build output for the HelmRelease; if flate does not apply postRenderers, `kustomize build` over the `helm template` output plus the same patches) shows, via `yq 'select(.kind=="Deployment" or .kind=="Job") | [.metadata.name, .spec.template.metadata.labels]'`, the gate labels on exactly api, worker, beat-worker, migrate (both) and live (dragonfly only)
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` exits 0
- [ ] E2E in Chunk 3

#### Files Changed Summary
```
feat(plane): add plane
kubernetes/apps/default/plane/**
kubernetes/apps/default/kustomization.yaml
```

#### Continuation Prompt
> Continue the Add Plane plan from `docs/plans/add-plane.md` in worktree `.claude/worktrees/feat-add-plane`.
> 1. Verify branch `feat/add-plane` and the worktree, `git pull --ff-only && git status`, `git log --oneline -5`
> 2. Mark Chunk 2 complete
> 3. Proceed with Chunk 3: validate, PR, merge, verify live

---

### Chunk 3: Validate, PR, merge, verify live
**Status:** Not Started
**Wave:** 3
**Estimated Context:** ~40k tokens
**Complexity:** Medium

#### Prerequisites
- Chunks 1 and 2. Owner OK to push and, later, to merge.

#### Detailed Steps
1. `/simplify` on the diff, then `validate-pr.sh`.
2. Ask the owner; push; `gh pr create` with a terse body. Shepherd to green CI and zero unresolved threads (`Kritika / Review`).
3. After the owner merges, watch in order: `Database/plane` Ready, role login, migrator Job `plane-api-migrate-1` Complete (the PostgreSQL 18 gate, finding 8), then the Deployments.
4. Worker: `ps` in the worker pod shows 2 prefork children; memory stays under the limit.
5. Renovate: after its next run, the Dependency Dashboard must list `plane-ce` with datasource `helm`. If it is missing, the HelmRelease was not matched to the HelmRepository (namespace matching); fix by setting `metadata.namespace` on both or `sourceRef.namespace`.

#### Verification
- [ ] `flux get hr -n default plane` Ready; all pods Ready; no restarts after 30 min
- [ ] `https://plane.${SECRET_DOMAIN}/` loads from the LAN; `/god-mode/` shows instance setup; `/api/instances/` returns JSON
- [ ] Dragonfly: only db 13 gained Plane keys; vMCP sessions in db 0 survive an api pod restart (`dbsize` on db 0 before and after)
- [ ] Hubble shows no dropped flows from Plane pods to `database` or to envoy-internal
- [ ] From the api pod, a TCP connect to `smtp-relay.network.svc.cluster.local:25` returns the Postfix `220` banner (Decision 9)
- [ ] api log shows `create_bucket` finding the existing bucket; `ObjectBucketClaim/plane-uploads` is `Bound`
- [ ] Not served on `envoy-external`

#### Files Changed Summary
```
none (review fixes amend Chunk 1 and 2 files)
```

#### Continuation Prompt
> Continue the Add Plane plan from `docs/plans/add-plane.md`. Verify the branch and worktree first.
> Mark Chunk 3 complete, then hand Chunk 4 to the owner (manual bootstrap).

---

### Chunk 4: Owner bootstrap (by hand)
**Status:** Not Started
**Wave:** 4
**Complexity:** Low

#### Detailed Steps (owner)
1. `https://plane.${SECRET_DOMAIN}/god-mode/`: create the instance admin. Turn off telemetry and public sign-up as wanted.
1a. God-mode, Email: enter the Decision 9 values and use the built-in test mail. With mail working, step 4's invitation arrives by mail and sign-up can stay disabled.
2. Create the workspace; slug `home` unless the owner picks another (OQ-6).
3. Upload an attachment and open a page in two tabs (checks storage and `/live/`).
4. Create the agent user: a second account with its own e-mail, invited to the workspace with role **Member** (not Admin). Without SMTP, enable sign-up for the moment, register, disable it again.
5. Logged in as the agent user: create a personal access token (API tokens in the account settings), no expiry or a noted one.
6. Create branch `feat/add-plane-mcp` from updated `origin/main` with worktree `.claude/worktrees/feat-add-plane-mcp` and a line in `~/.claude/sessions.md`. Add `PLANE_API_KEY: <token>` to `kubernetes/apps/ai/toolhive/app/secret.sops.yaml` with `sops` and commit it there. This edits an existing SOPS secret, so it is the owner's step.

#### Verification
- [ ] `curl -H "X-API-Key: <token>" https://plane.${SECRET_DOMAIN}/api/v1/workspaces/<slug>/projects/` returns 200
- [ ] Attachment upload and download work; two tabs sync
- [ ] God-mode test mail arrives; the relay log (`kubectl logs -n network deploy/smtp-relay`) shows `status=sent`

---

### Chunk 5: ToolHive MCPServer
**Status:** Not Started
**Wave:** 5
**Estimated Context:** ~25k tokens
**Complexity:** Low

#### Scope
- `kubernetes/apps/ai/toolhive/mcp/plane.yaml` - new
- `kubernetes/apps/ai/toolhive/mcp/kustomization.yaml` - add `plane.yaml`

#### Prerequisites
- Chunk 4 (the key must be in `toolhive-secrets` first, or the pod sits in `CreateContainerConfigError`). Branch `feat/add-plane-mcp` and its worktree exist from Chunk 4.

#### Detailed Steps
1. Copy `mealie.yaml`. Set:
   - `image: docker.io/makeplane/plane-mcp-server:v0.3.6@sha256:<digest re-checked on Docker Hub>`
   - `transport: stdio`, `args: ["stdio"]` (finding 5; the image's default `CMD` is `http`), with a one-line comment why
   - `groupRef.name: all`
   - `env`: `PLANE_BASE_URL: http://plane-api.default.svc.cluster.local:8000`, `PLANE_WORKSPACE_SLUG: <slug from Chunk 4, default home>`
   - `secrets`: `toolhive-secrets` key `PLANE_API_KEY`
   - resources as `mealie.yaml`
   - comment: token of a non-admin Plane user, so admin actions fail server-side
2. The `gvisor` and `automountServiceAccountToken: false` patches in `mcp/kustomization.yaml` apply by themselves. `default-ingress-allowlist` admits namespace `ai`.
3. `validate-pr.sh`, owner OK, push, PR, shepherd.

#### Verification
- [ ] `kubectl get mcpserver -n ai plane` Ready after merge
- [ ] Through the unified vMCP: `find_tool` returns Plane tools; `project(action="list")` returns the workspace's projects
- [ ] A workspace-admin action is refused by Plane
- [ ] Renovate lists the image

#### Files Changed Summary
```
feat(toolhive): add plane mcp server
kubernetes/apps/ai/toolhive/mcp/{plane.yaml,kustomization.yaml}
```

#### Continuation Prompt
> Continue the Add Plane plan from `docs/plans/add-plane.md`. Verify branch `feat/add-plane-mcp` and its worktree first.
> Mark Chunk 5 complete; Chunk 6 runs no earlier than 7 days after Chunk 3.

---

### Chunk 6: Sizing and docs
**Status:** Not Started
**Wave:** 6
**Complexity:** Low

#### Detailed Steps
0. Branch `chore/plane-sizing` from `origin/main`, own worktree and session claim.
1. Owner rule (2026-11-10): memory request = a round value just below the 7-day average of working set minus active file; limits unchanged. Query per container, for example
   `avg_over_time((container_memory_working_set_bytes{namespace="default",pod=~"plane-.*"} - container_memory_active_file_bytes{...})[7d:5m])`, and the same for the MCP pod in `ai`. Record each number with its query in the PR body.
2. Final documentation pass per the Process Instructions; remove this plan file.

#### Verification
- [ ] Every Plane request is below its measured 7-day average and round; no OOM kill in the window
- [ ] `validate-pr.sh` exits 0

---

## Testing Strategy

### Unit Tests
- None. `flate test all`, `secret-check.py` and `validate-pr.sh` are the static gates.

### E2E Verification
- [ ] Login, work item, attachment round trip, page live sync (Chunk 4)
- [ ] api restart does not flush db 0 (Chunk 3)
- [ ] MCP list call through the vMCP (Chunk 5)

---

## Rollback Plan

Order matters: Plane first, the object store last. Every live step needs the owner's OK.

1. Remove Plane: revert the Plane, MCP and CNPG commits. Flux prunes the HelmRelease, route, claim and MCPServer.
2. PVCs from `volumeClaimTemplates` are not pruned: delete `pvc-plane-*` by hand.
3. The bucket outlives the claim (`reclaimPolicy: Retain`): `radosgw-admin bucket rm --bucket=plane-uploads --purge-objects` from the toolbox.
4. Only then revert the RGW commit. `preservePoolsOnDelete: true` keeps the pools; removing them is a manual `ceph osd pool rm`.
5. The database is kept (`databaseReclaimPolicy: retain`): the owner drops database and role `plane` by hand.
6. `FLUSHDB` on Dragonfly db 13. Remove `PLANE_API_KEY` from `toolhive-secrets` when convenient.

---

## Open Questions for the Owner

- **OQ-2:** Dragonfly db index 13 free? Clients configured inside SOPS secrets are not visible in the repo. Chunk 1 step 5 checks `dbsize`.
- **OQ-6:** slug `home` accepted 2026-11-22. Still open: the agent user's e-mail, to be a real address the owner can receive on (mail works via the relay); chosen at bootstrap (Chunk 4).
- **OQ-7:** closed 2026-11-22, Homepage group `Utilities`.
- **OQ-10:** closed 2026-11-22 (default accepted): port 8211 is unused with stdio.

### Answered 2026-11-22

- **OQ-1:** Ceph RGW (Chunk 0).
- **OQ-3:** PostgreSQL 18.7 accepted; the migrator Job is the gate.
- **OQ-4:** Dragonfly db 13 accepted as is (cache mode, shared password, no ACL).
- **OQ-5:** No MCP tool filter.
- **OQ-7 (mail):** SMTP through the in-cluster relay (Decision 9).
- **OQ-8, OQ-9:** closed by the RGW choice (signed GETs on a private bucket; presigned URLs use a hostname the MCP pod can reach, checked in Chunk 5).
- **OQ-11:** No backup for uploads, accepted.
- **OQ-12:** RGW `priorityClassName: homelab-optional`, one gateway instance.
- **OQ-13:** `egress.home.arpa/lan: allow` on api and worker only, and only if Chunk 0's reachability test fails.

---

## Review Report (2026-11-22, automated, opus)

**Result:** 0 CRIT, 2 HIGH, 5 MED, 5 LOW. All HIGH and MED folded into the plan above.

Confirmed by the reviewer with `helm template plane /tmp/plane-chart/plane-ce -n default -f <plan values>`: workload names `plane-{admin,api,beat-worker,live,space,web}-wl`, StatefulSets `plane-{minio,rabbitmq}-wl`, Jobs `plane-api-migrate-1` and `plane-minio-bucket-1`, ServiceAccount `plane-srv-account`, PVC `pvc-plane-minio-vol-plane-minio-wl-0`; Kustomization `dragonfly-cluster`; `ai` to `default` traffic is admitted (`toolhive-ingress-deny` only covers ingress to ToolHive pods); both label gates match.

- HIGH-1 presigned URL scheme: [FIXED] `minio.env.minio_endpoint_ssl: true`. (`plane/settings/production.py:15` also sets `SECURE_PROXY_SSL_HEADER`.)
- HIGH-2 install timeout: [FIXED] `timeout: 15m`, `install.remediation.retries: 2`.
- MED-1 Renovate auto-merge of chart patches: [FIXED] `plane-ce` packageRule, `automerge: false`.
- MED-2 worker concurrency: [FIXED] `--concurrency=2` patched from the start.
- MED-3 kopiur files missing from scope: [FIXED] added to write set with literal values and a DR note.
- MED-4 bucket readable without login: [OPEN] OQ-8.
- MED-5 SOPS substitution precedent overstated: [FIXED] reworded; Chunk 3 check is the gate.
- LOW-1 storageClass wording, LOW-2 `assign_cluster_ip` scope, LOW-3 bucket Job init container, LOW-4 API rate limit: [FIXED]. LOW-5 MCP attachment upload: [OPEN] OQ-9.
- AMB-1 to AMB-9 and PIT-1 to PIT-4 (JSON6902 `~1` escaping, `$$`, literal kopiur values, per-target container patches): applied in Chunks 1, 2, 4, 5, 6.

Still unverified, each gated by a check in the plan: `django_redis` `clear()` = `FLUSHDB` (Chunk 2 step 8), Renovate pairing HelmRelease with HelmRepository (Chunk 3 step 5), flate rendering from a HelmRepository (Chunk 2 verification), Dragonfly `--cluster_mode=emulated` accepting `SELECT 13` (indexes 11 and 12 are live).

### Revision 2026-11-22 (after the review)

Second revision the same day: owner answers recorded (open-question list trimmed), Decision 9 (mail through the relay, configured by hand in god-mode) added; also unreviewed.

The owner chose Ceph RGW. Added Chunk 0, Decisions 7 and 8, the claim-based credentials and the same-origin route; removed the bundled object store, its kopiur policy and the `plane-doc-store` SOPS Secret. Review items about the bundled store (HIGH-1, MED-3, MED-4, LOW-3, LOW-5) no longer apply. **Chunk 0 and Decisions 7 and 8 have not been through the automated review.**

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
