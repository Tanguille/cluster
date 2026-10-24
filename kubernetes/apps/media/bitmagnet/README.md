# Bitmagnet stack

One app-template HelmRelease manages Bitmagnet and the Armiger connector.
The database is `bitmagnet`, owned by the non-superuser `bitmagnet` role on the
existing shared CNPG cluster `postgres16` (currently PostgreSQL **18**, despite
its retained name). No dedicated PostgreSQL deployment or cluster is created.
Application services remain `bitmagnet:3333` and
`armiger-connector:8096` / `:8097`. Prowlarr can use the connector's `/torznab`
endpoint. The UI is routed through `envoy-internal` at
`bitmagnet.${SECRET_DOMAIN}`; it is not internet-facing or authenticated by this
stack. Keep it on the trusted internal network.

## Credentials and cutover gate

The shared ResourceSet provisions the database with reclaim policy `retain`,
following peer apps. Its managed login role is not a superuser and cannot create
databases or roles. The HelmRelease is suspended until existing data and resource
ownership have been checked and any migration is complete. Suspension does not stop
already running workloads; it prevents this change removing the old database
Deployment before migration. The Flux dependency is `cloudnative-pg-databases`.

The registered SOPS-encrypted `database/cloudnative-pg/cluster/roles/bitmagnet.sops.yaml`
(relative to `kubernetes/apps`) defines `database/bitmagnet-db`, type
`kubernetes.io/basic-auth`, with username `bitmagnet` and the existing app password.
It was created with approval using in-memory decryption/encryption; the original
app secret is unchanged. The managed role references this secret through
`passwordSecret.name: bitmagnet-db`. The app still reads the existing
`media/bitmagnet-postgres` secret, but connects as `bitmagnet`, never `postgres`.
CNPG requires the role secret in its own namespace with `username`/`password`
keys; the existing media secret cannot be referenced directly.
Password rotation must update both encrypted secrets together.

## Migration and rollback

Before resuming Helm, inspect the actual target cluster and data; absence has
not been established. With approval, stop Bitmagnet and connector writers, keep
the source PG16 accessible, and take a logical dump. Restore into the shared
`bitmagnet` database as its owner using `pg_restore --no-owner --no-acl`, then
verify extensions, schema/migrations, row counts and application compatibility
with PG18. Use suitable migration-admin access only for the migration, not the
runtime app. Never mount PG16 files into PG18. Rehearse on an isolated database
before cutover; do not initialize an empty target over unexamined existing data.

Only after credential and data verification should `spec.suspend` be removed.
The Helm upgrade then retires its standalone postgres Deployment/Service.
If resources are still raw/unmanaged, separately arrange approved ownership
adoption/replacement; prevent old Flux inventory pruning newly adopted resources.

`bitmagnet-pgdata` remains 5Gi with Flux prune protection, but no new workload
mounts it. Retain it and its credentials for rollback until explicit retirement
approval. `armiger-connector-data` is unchanged and also prune-protected.
Rollback requires stopping writers and restoring the old release/ownership with
PG16 against the retained claim. Writes made after cutover require a deliberate
data-reconciliation plan: restoring the old PVC alone would lose those writes.

## DHT networking

HTTP uses ClusterIP + HTTPRoute, not NodePort. DHT uses TCP and UDP 3334 on the
scheduled node via `hostPort`, supported by this cluster's Cilium kube-proxy
replacement. Only these ports are allowed from `world` for Bitmagnet pods; the
namespace ingress allowlists still govern HTTP, the shared database and connector.
There is no host networking, new LB allocation or unrestricted ingress rule.

Check node port conflicts and actual DHT reachability during rollout. Node
placement may change on restart; any upstream NAT forwarding must follow the
scheduled node. No router/firewall configuration is changed here and public
inbound connectivity is not guaranteed by these manifests alone.

## Existing shared backups

There are no app-specific SnapshotPolicies, SnapshotSchedules or dump hooks.
The shared CNPG cluster already has daily Barman backups, continuous WAL
archiving to R2 and seven-day retention. This database participates after
migration; existing backup health and recoverability must still be verified.
Recover individual data through an isolated cluster restore, not by rolling
back the shared production cluster and its other applications. The retained
legacy PVC and connector PVC have no backup policy in this stack.

## Supply chain and runtime limitations

Images are pinned by registry digest. Connector source is pinned to
`fd5c3faae6f5bd915a66010b7d1ed67ed8dd96f8` and SHA-256 verified on every bootstrap.
The Hugging Face bundle is pinned to `cdf7036c45a0d9751d18e738c7abfde485552964`;
automatic floating model downloads are disabled in the application. Bootstrap
markers are written only after successful work; existing markers were not a
missing feature in the original stack.

Direct Python requirements and the CPU Torch wheel version are pinned to
published releases. Transitive dependencies are **not** hash-locked; this is
still a network-dependent runtime bootstrap, not a reproducible prebuilt image.
The pinned HF bundle contains executable Python, not just model weights, and
has not been independently audited. A successful package resolution does not
establish model/API compatibility or throughput. Check actual inference,
`/healthz`, GraphQL tagging and Torznab responses during rollout. The inherited
malware-pruning setting can delete indexed records; it has not been changed.

When changing Python requirements/image ABI, bump `runtime-v1` consistently in
bootstrap and Helm values so a stale success marker cannot suppress installation.
Versioned runtime/model paths preserve rollback data but consume extra PVC space.
