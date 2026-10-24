# Bitmagnet stack

One app-template HelmRelease manages PostgreSQL 16, Bitmagnet and the Armiger
connector. Services remain `bitmagnet-postgres:5432`, `bitmagnet:3333` and
`armiger-connector:8096` / `:8097`. Prowlarr can use the connector's `/torznab`
endpoint. The UI is routed through `envoy-internal` at
`bitmagnet.${SECRET_DOMAIN}`; it is not internet-facing or authenticated by this
stack. Keep it on the trusted internal network.

## Deployment gate and rollback

This is **not** a claim that the stack is deployed or the existing data is empty.
Before allowing Flux to reconcile this refactor, an operator must inspect the
target context, existing resources/Flux inventory, PVC contents, ownership and
free space, and take a verified backup. Do not delete or reinitialize either PVC.

Raw Deployment selectors and Helm ownership differ. If the raw stack exists,
suspend its reconciliation, stop the three old Deployments in a maintenance
window, and arrange their controlled replacement plus Service ownership adoption
before resuming the HelmRelease. Ensure the old Flux inventory cannot prune
resources now owned by Helm. These are live mutations requiring explicit approval;
simply merging over an existing raw deployment is not a safe adoption procedure.
For an absent stack, normal Flux reconciliation creates the retained PVCs.

`bitmagnet-pgdata` remains 5Gi on `ceph-block`, mounted at `/var/lib/postgresql`
with `PGDATA=/var/lib/postgresql/data`; PostgreSQL stays on major 16 and uid 70.
`armiger-connector-data` also remains 5Gi. Both are managed outside Helm, with
Flux pruning disabled. Check existing ownership before enforcing uid/fsGroup
70 (database) and 568 (connector); do not blindly recursively chown live PGDATA.

For rollback, suspend the new HelmRelease/reconciliation first, stop its
workloads, revert the Git change, and perform the reverse ownership transition
under the same maintenance window. Never run old and new PostgreSQL processes
against one claim. Restore the pre-change backup if data requires rollback.
Existing connector `.venv`, script and model paths are untouched: the new runtime
uses `runtime-v1` and a revision-specific model directory.

## DHT networking

HTTP uses ClusterIP + HTTPRoute, not NodePort. DHT uses TCP and UDP 3334 on the
scheduled node via `hostPort`, supported by this cluster's Cilium kube-proxy
replacement. Only these ports are allowed from `world` for Bitmagnet pods; the
namespace ingress allowlist still governs HTTP, PostgreSQL and the connector.
There is no host networking, new LB allocation or unrestricted ingress rule.

Check node port conflicts and actual DHT reachability during rollout. Node
placement may change on restart; any upstream NAT forwarding must follow the
scheduled node. No router/firewall configuration is changed here and public
inbound connectivity is not guaranteed by these manifests alone.

## Backups and recovery

Kopiur's repository is a Flux dependency. Hourly CSI snapshots of both claims
are uploaded to `kopia-nas`, retaining 24 hourly / 7 daily copies and weekly quick
verification. No restore dataSource is added to either existing PVC.

Before each database snapshot, a fail-closed hook runs `pg_dump -Fc` as the
database user and atomically replaces `/var/lib/postgresql/backup/bitmagnet.dump`.
The CSI snapshot contains PGDATA and WAL on the same volume (crash recovery),
plus a transaction-consistent logical dump as an alternative recovery path.
Allow room for the current and temporary dump in addition to PGDATA: 5Gi may
need expansion as crawling grows. A failed/15-minute timed-out dump fails the
backup, rather than reporting success with an old dump.

Use the [Kopiur restore runbook](../../../../docs/kopiur-restore.md), with the
explicit PVC names above and policies `bitmagnet-postgres` / `armiger-connector`.
Stop all writers before recovery. Restore full PG16 storage using the unchanged
secret, or recover `backup/bitmagnet.dump` into a separately initialized PG16
database with `pg_restore`; validate the recovered database before switching
the application. A successful first snapshot and an isolated restore test remain
rollout requirements, not results established by manifest validation.

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
