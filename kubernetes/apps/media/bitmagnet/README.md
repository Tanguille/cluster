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

## Database and credentials

The shared ResourceSet provisions the database with reclaim policy `retain`,
following peer apps. Its managed login role is not a superuser and cannot create
databases or roles. The Flux dependency is `cloudnative-pg-databases`.

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

## Fresh installation

Read-only inspection of `admin@kubernetes` found no Bitmagnet or Armiger
workloads, claims, backing PVs, Flux inventory, or shared database/role.
There was nothing to delete or migrate on that cluster. This configuration
therefore enables a fresh installation without a legacy PostgreSQL PVC or
Helm suspension. Recheck for conflicts if deploying to a different cluster.

Flux follows `main`: pushing this PR branch alone does not deploy the stack.
After merge, verify database/role readiness, Helm readiness, initial schema
creation on PostgreSQL 18, Armiger classification and Torznab filtering.
Manifest validation is not proof of runtime compatibility.

The 5Gi `armiger-connector-data` claim retains Flux prune protection. Do not
delete it or the shared database as part of routine rollback. If rollout fails,
stop writers and inspect the failure before changing data; shared CNPG and
other applications must remain untouched.

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
archiving to R2 and seven-day retention. This database participates once
created; existing backup health and recoverability must still be verified.
Recover individual data through an isolated cluster restore, not by rolling
back the shared production cluster and its other applications. The connector
PVC has no backup policy in this stack.

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

When changing Python requirements/image ABI, bump `runtime-v2` consistently in
bootstrap and Helm values so a stale success marker cannot suppress installation.
Versioned runtime/model paths preserve rollback data but consume extra PVC space.
