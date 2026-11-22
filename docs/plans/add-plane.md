# Add Plane Implementation Plan

**Created:** 2026-11-22
**Status:** In Progress (repo files committed on `feat/add-plane`; live steps and owner steps open)
**Complexity:** High
**Branch:** `feat/add-plane` (from `origin/main`), one draft PR
**Worktree:** `.claude/worktrees/feat-add-plane`

---

## Overview

Plane CE (chart `plane-ce` 1.9.1, app v1.5.0) in namespace `default` behind an internal-only
HTTPRoute, on the existing CNPG and Dragonfly, with the chart's bundled RabbitMQ and uploads on a new
Ceph RGW object store. A ToolHive `MCPServer` exposes it to agents as a non-admin user.

The manifests are written and validated offline (`validate-pr.sh`, `flate test all`). Nothing has
run on the cluster: every item under "Live verification" is still open.

Not verified anywhere yet: the claims marked "unverified" below. The RGW part (Decisions 3, 7, 8)
was added after the automated plan review and has had one read-only review, not an independent re-review.

---

## Technical Analysis

Each finding names its source.

1. **Cloudflare R2 cannot serve Plane uploads.** Plane uploads with a presigned POST
   (`apps/api/plane/settings/storage.py` at `v1.5.0`, `generate_presigned_post`). R2 docs,
   <https://developers.cloudflare.com/r2/api/s3/presigned-urls/>: "`POST` (multipart form uploads
   via HTML forms) is not currently supported."
2. **The browser talks to the S3 endpoint directly** (presigned POST and GET), so the endpoint must
   be reachable from LAN browsers, not only from pods.
3. **No Ceph RGW existed** (`cephObjectStores: []` in
   `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`); this change adds one.
4. **Plane flushes its Redis database on every api start.** `bin/docker-entrypoint-api.sh` runs
   `python manage.py clear_cache`, which calls `cache.clear()`; with the `django_redis` backend
   that is `FLUSHDB` (unverified in the image). Db 0 holds the vMCP sessions
   (`virtualmcpservers.yaml`, `keyPrefix: thv:vmcp:unified:session:`), so Plane gets its own index, 13
   (11 = searxng, 12 = tracearr).
5. **The MCP server's `http` transport ignores `PLANE_API_KEY`.** README at `v0.3.6`: the env keys
   are "Required for: stdio"; the remote transports want `Authorization` and `X-Workspace-slug`
   headers per request, which ToolHive does not inject. So `transport: stdio`, `args: ["stdio"]`.
6. **The chart has no securityContext, pod-label or env knobs**, its Services are headless unless
   `assign_cluster_ip: true`, and its ServiceAccount automounts a token. Labels and hardening go
   through Flux `postRenderers`, as in `kubernetes/apps/default/nextcloud/app/helmrelease.yaml`.
7. **Flux substitutes after SOPS decryption** (kustomize-controller
   `internal/controller/kustomization_controller.go`: `DecryptResource`, then `SubstituteVariables`).
   So `${DRAGONFLY_PASSWORD}` inside a SOPS `stringData` value is substituted. Every other value in
   those secrets is hex, free of `$`.
8. **The CNPG cluster `postgres16` runs PostgreSQL 18.7**; Plane's chart ships 15.7. Accepted by the
   owner (2026-11-22) with the migrator Job as the gate.
9. **The Celery worker starts with no `--concurrency`** (`bin/docker-entrypoint-worker.sh`), so it
   forks one process per node core. A postRenderer patch runs the entrypoint's own steps with
   `--concurrency=2`.

---

## Decisions & Trade-offs

### Decision 1: Postgres path
**Chosen:** `pgbouncer-session.database.svc.cluster.local:5432` with `?sslmode=require`, as nextcloud.
**Rationale:** Django plus Celery needs session semantics (server-side cursors; Plane has no
`DISABLE_SERVER_SIDE_CURSORS` switch), which rules out `pgbouncer-rw` (transaction mode). Direct
`postgres16-rw` would skip the pool ceiling against `max_connections: 150`. The poolers set
`client_tls_sslmode: require`. The existing gate in `kubernetes/apps/database/networkpolicy.yaml`
admits pods labelled `egress.home.arpa/postgres: allow`.

### Decision 2: How the passwords reach `DATABASE_URL` and `REDIS_URL`
**Chosen:** `REDIS_URL: redis://:${DRAGONFLY_PASSWORD}@dragonfly.database.svc.cluster.local:6379/13`
inside the SOPS secret, substituted by Flux (finding 7). The DB password is generated once and
written into `roles/plane.sops.yaml` and `plane-app-env`.
**Rationale:** one line and no decryption of an existing secret. `components/dragonfly-auth/secret.yaml`
uses the same variable in a plain Secret; no SOPS file in the repo is known to rely on it. It fails
loudly (Redis AUTH errors). The chart's checksum annotation only covers secrets it renders itself,
so api, worker, beat-worker and live carry `reloader.stakater.com/auto: "true"`.

### Decision 3: Object storage for uploads
**Chosen (owner, 2026-11-22):** Ceph RGW, a `CephObjectStore` in the existing rook-ceph cluster. R2
is out (finding 1).
- **Pools:** metadata and data replicated `size: 2`, `failureDomain: host`, like the block pool. The
  chart's example (erasure-coded 2+1) has `min_size 3` on three hosts and stops serving I/O whenever
  one node is down. Size 2 stays available through single-node maintenance at the known cost: one
  copy during that window. The data pool uses `compression_mode: aggressive` with zstd, like the
  other pools in that file.
- **No backup** for uploads (kopiur covers PVCs only); accepted by the owner.
- **Gateway:** one instance, `homelab-optional`, port `8080` (host network, so not the privileged
  80; the chart's route port follows `gateway.port`).
- **Ceph v21:** the CephFS zero-read bug and the `readAffinity` kernel floor concern the in-kernel
  client, not RGW. Whether RGW on v21.2.0 starts cleanly under `allowedCiphers: ["aes256k"]` is unverified.
- RGW creates about seven pools; with three OSDs the PG autoscaler must stay under
  `mon_max_pg_per_osd` (checked live).

### Decision 4: RabbitMQ PVC backup
**Chosen:** none. It is only the Celery broker (100Mi PVC, chart default); losing it drops in-flight
background jobs and Plane re-declares its queues on start.

### Decision 5: Chart source and Renovate
**Chosen:** `HelmRepository` (`https://helm.plane.so/`, the repo's first) plus classic `chart.spec`;
no OCI chart exists. No `planeVersion` or image overrides: the Plane and RabbitMQ images are chart
defaults and move with the chart, so a chart patch can carry migrations. Hence the Renovate rule
`plane-ce`, `automerge: false`. Digest pinning is not possible (one shared tag for seven images);
`pullPolicy: IfNotPresent`. Renovate pairing the HelmRelease with the HelmRepository is unverified.

### Decision 6: MCP tool filter
**Chosen (owner):** none, like `mealie.yaml`. The server has no include/exclude env, and a
`MCPToolConfig` filters whole tools while Plane's 30 tools each take an `action`, so it could not
block `delete` anyway. Access control is the agent user's role (Member, not Admin).

### Decision 7: Bucket and credentials
**Chosen:** `ObjectBucketClaim` `plane-uploads` in `default` (`storageClassName: ceph-bucket`,
`reclaimPolicy: Retain`).
**Rationale:** Rook writes the key Secret into the claim's namespace, and the chart reads it through
`external_secrets.storage.*` on api, worker, beat-worker and the migrator Job. A
`CephObjectStoreUser` would put the key in `rook-ceph` and need a cross-namespace copy. The other
doc-store keys are not secret and come from chart values. The Secret is created by Rook, not Flux:
pods wait in `CreateContainerConfigError` until it exists.

### Decision 8: How the browser reaches the bucket
Uploads and downloads are presigned for the single `AWS_S3_ENDPOINT_URL`, so browser and pods use
the same URL and SigV4 signs its host.

**Chosen:** same origin. `AWS_S3_ENDPOINT_URL: https://plane.${SECRET_DOMAIN}`, and the rook chart's
own `cephObjectStores[].route` serves `PathPrefix /plane-uploads` on that hostname (rendered in
`rook-ceph`, next to the dashboard route). boto3 uses path-style addressing for a custom endpoint.
**Rationale:** no CORS (Rook has no declarative CORS for a claim), no second hostname, no
cross-namespace `backendRef`. Routing only that path is not a fence: RGW is on the host network
(`network.provider: host`), where Cilium enforces no endpoint policy, so any pod or LAN host can
reach node port 8080. The S3 credentials are the only gate, like the mgr dashboard's login.

**Unverified, checked live:**
- RGW accepts a path-style presigned POST under Plane's hostname.
- Plane pods reach `https://plane.${SECRET_DOMAIN}` without `egress.home.arpa/lan`.
  `deny-lan-egress` blocks `192.168.0.0/16`, which contains the envoy-internal address; its separate
  endpoint rule for `envoy-internal-tls` suggests gateway traffic is matched as an endpoint. If the
  check fails, add `egress.home.arpa/lan: allow` to api and worker only (owner-approved for that case).

### Decision 9: Outgoing mail
**Chosen (owner):** the relay `smtp-relay.network.svc.cluster.local`
(`kubernetes/apps/network/smtp-relay`, Postfix to Gmail).
- **Relay:** port 25, no client auth (`POSTFIX_mynetworks: "${POD_CIDR}"`), no TLS on the listening side.
- **Network:** nothing to add. Egress from `default` is default-allow, the relay is not in the
  ranges `deny-lan-egress` blocks, and namespace `network` has no CiliumNetworkPolicy.
- **Config lives in the database, set by hand in god-mode.** `SKIP_ENV_VAR` defaults to `"1"`
  (`settings/common.py:401`), so `get_email_configuration()` (`license/utils/instance_value.py:42`)
  reads the `InstanceConfiguration` rows. Env `EMAIL_*` values only seed those rows on first
  creation, and the chart has no email values; seeding is not used.
- **Values:** host as above, port `25`, no username or password, TLS and SSL off, sender = the Gmail
  account the relay logs in as (Gmail rewrites any other From; it stays out of git).

### Hardening as shipped
`seccompProfile: RuntimeDefault` on every pod; `allowPrivilegeEscalation: false` and `drop: [ALL]`
on all Plane containers except rabbitmq (escalation off only: its entrypoint starts as root and
needs capabilities to drop). `runAsNonRoot` is set nowhere. The namespace's PSA `baseline` passes.
Rendered, never run.

---

## Live verification

Every step touches the cluster or needs the owner; each needs the **owner's OK**. Steps marked
(owner) are done by the owner by hand.

### Before merge
- [ ] (owner) Add `PLANE_API_KEY` to `kubernetes/apps/ai/toolhive/app/secret.sops.yaml`; a
      placeholder is enough. Without it the `plane` MCP pod sits in `CreateContainerConfigError`
      until bootstrap is done.
- [ ] Dragonfly db 13 is empty: `redis-cli -n 13 dbsize` returns 0 (needs the Dragonfly password).
- [ ] `ceph status` is `HEALTH_OK`, no node maintenance planned that day.

### Object store
- [ ] `CephObjectStore/ceph-objectstore` `Ready`; RGW pod Running, no cephx or cipher error in its log.
- [ ] `ceph status` `HEALTH_OK`; `ceph osd pool autoscale-status` shows the new pools at size 2, no
      `TOO_MANY_PGS`; no new slow-op alerts in 30 min.
- [ ] `curl -si https://plane.${SECRET_DOMAIN}/plane-uploads/` from the LAN returns `403` with an
      `x-amz-request-id` header. Do not match on the body: envoy's `eep` wasm filter replaces 404
      and 5xx bodies. Nothing is served on `envoy-external`.
- [ ] The same `curl` from a `default` pod without `egress.home.arpa/lan` succeeds (Decision 8).
- [ ] Presigned POST: with a throwaway claim in `rook-ceph`, generate a presigned POST for
      `https://plane.${SECRET_DOMAIN}` with boto3, upload a file larger than 1 MiB with `curl -F`,
      download and compare, delete claim and bucket. If RGW rejects it, stop and ask the owner
      (fallback: own hostname plus a hand-set CORS rule).

### Plane
- [ ] `Database/plane` Ready; migrator Job `plane-api-migrate-1` Complete (the PostgreSQL 18 gate).
- [ ] `ObjectBucketClaim/plane-uploads` `Bound`; api log shows `create_bucket` finding the bucket.
- [ ] `flux get hr -n default plane` Ready; all pods Ready, no restarts after 30 min (this is also
      the test of the dropped capabilities).
- [ ] In the image, `django_redis/client/default.py` `clear()` calls `flushdb()`.
- [ ] Only db 13 gained Plane keys; `dbsize` on db 0 is unchanged across an api pod restart.
- [ ] Worker shows 2 prefork children and stays under its memory limit.
- [ ] Hubble shows no dropped flows from Plane pods to `database` or envoy-internal; from the api
      pod, `smtp-relay.network.svc.cluster.local:25` answers with the Postfix `220` banner.
- [ ] `https://plane.${SECRET_DOMAIN}/` loads from the LAN and is not served on `envoy-external`.
- [ ] After Renovate's next run the Dependency Dashboard lists `plane-ce` (datasource `helm`). If
      not, set `metadata.namespace` on both objects or `sourceRef.namespace`.

### Bootstrap (owner, by hand)
1. **Immediately after Plane is up, claim the instance admin at
   `https://plane.${SECRET_DOMAIN}/god-mode/`.** The first visitor can claim it; the route is
   LAN-only (envoy-internal), so the window is small but real.
2. **Before anything else, disable public sign-up in god-mode.** Turn off telemetry as wanted.
3. God-mode, Email: enter the Decision 9 values and send the built-in test mail. Check: mail
   arrives and `kubectl logs -n network deploy/smtp-relay` shows `status=sent`.
4. Create the workspace, slug `home` (it is what `PLANE_WORKSPACE_SLUG` in
   `kubernetes/apps/ai/toolhive/mcp/plane.yaml` says; change both together).
5. Upload an attachment and open a page in two tabs (storage and `/live/`).
6. Invite the agent user (its own e-mail) with role **Member**, not Admin. With mail working the
   invitation arrives by mail and sign-up stays disabled.
7. As the agent user, create a personal access token. Check:
   `curl -H "X-API-Key: <token>" https://plane.${SECRET_DOMAIN}/api/v1/workspaces/home/projects/` returns 200.
8. Put the token in `toolhive-secrets` as `PLANE_API_KEY` with `sops` (existing SOPS file, owner only).

### MCP server
- [ ] `kubectl get mcpserver -n ai plane` Ready.
- [ ] Through the unified vMCP: `find_tool` returns Plane tools; `project(action="list")` returns
      the workspace's projects (the name may carry a `plane_` prefix).
- [ ] A workspace-admin action is refused by Plane; an attachment upload works.

### After 7 days
- [ ] Set requests by the owner's rule (2026-11-10): a round value just below the 7-day average of
      working set minus active file, limits unchanged, for the Plane pods, the MCP pod and RGW.
      Record each number with its query. Current requests are provisional.
- [ ] Fold this plan into `docs/` and remove it.

---

## Rollback Plan

Order matters: Plane first, the object store last. Every live step needs the owner's OK.

1. Remove Plane: revert the Plane, MCP and CNPG commits. Flux prunes the HelmRelease, route, claim
   and MCPServer.
2. PVCs from `volumeClaimTemplates` are not pruned: delete `pvc-plane-*` by hand.
3. The bucket outlives the claim (`reclaimPolicy: Retain`):
   `radosgw-admin bucket rm --bucket=plane-uploads --purge-objects` from the toolbox.
4. Only then revert the RGW commit. `preservePoolsOnDelete: true` keeps the pools; removing them is
   a manual `ceph osd pool rm`.
5. The database is kept (`databaseReclaimPolicy: retain`): the owner drops database and role `plane`.
6. `FLUSHDB` on Dragonfly db 13. Remove `PLANE_API_KEY` from `toolhive-secrets` when convenient.

---

## Open Items

- **Dragonfly db index 13 free?** Clients configured inside SOPS secrets are not visible in the
  repo; the `dbsize` check under "Before merge" settles it.
- **Agent user's e-mail address** (workspace slug defaults to `home`).

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
