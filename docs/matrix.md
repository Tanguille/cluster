# Matrix (Continuwuity and Sable)

Continuwuity runs in `default` using the shared `app-template` chart. Its public
address and permanent Matrix server name are both `matrix.${SECRET_DOMAIN}`;
accounts therefore have ids such as `@alice:matrix.example.com`. Confirm this
identity before the first deployment: changing the server name later requires a
new database. The root domain does not need Matrix discovery routes.

Federation is enabled over HTTPS on port 443 via `envoy-external`, including its
gateway-wide `crowdsec` extAuth SecurityPolicy. CrowdSec can deny requests before
they reach Continuwuity. Continuwuity serves both client and server
`/.well-known/matrix/` documents. Keep the hostname
accessible to Matrix clients and remote servers without a browser-only login or
challenge in front of it. Registration is closed except for the first-admin
bootstrap below. Authentication uses Continuwuity's local accounts; OIDC,
bridges and calling infrastructure are not configured.

## Sable web client

Sable v2 is served at `https://sable.${SECRET_DOMAIN}` via `envoy-external`.
Its runtime `config.json` selects `matrix.${SECRET_DOMAIN}` by default while
allowing users to choose another homeserver. Sign in with a local Matrix account
after the administrator bootstrap below.

The official web image serves static files on port 8080. Caddy's writable
directories are ephemeral, so Sable needs no persistent volume or backup. Client
state and encryption keys stay in the browser: set up recovery and retain the
recovery key before clearing browser storage or moving devices. Push, GIFs and
embeds are not configured.

## First administrator

After the deployment is approved and reconciled:

1. Read the Continuwuity pod logs locally:

   ```bash
   mise exec -- kubectl -n default logs deployment/continuwuity
   ```

2. Find the first-run registration token. Treat it as a credential; do not paste
   it into issues or chat. Use a Matrix client supporting registration tokens,
   such as the hosted Sable client or Element Web, and select
   `https://matrix.<your-domain>` as the homeserver.
3. Register your intended administrator account with that token. The first
   account becomes administrator, joins the admin room, and ends first-run mode.
   Subsequent public registration is denied, even with the old bootstrap token.
4. Create additional local accounts through the private admin room using
   `!admin users create-user` (use its help for the current arguments).

No bootstrap password or signing key is stored in Git: generated server identity
and account data live on the persistent volume. No SOPS change is needed for the
initial deployment.

## Storage and recovery

The shared Kopiur component provisions a 5 GiB `ceph-block` RWO volume and hourly
snapshots. Database, WAL, signing identity and media remain together under `/data`.
The workload uses `Recreate`, and backup/restore movers use the same UID/GID 1000.
Increase `PVC_CAPACITY` in `ks.yaml` when media growth requires it.

CSI snapshots are **crash-consistent**, not application-consistent: recent writes
still buffered in memory may be absent. Do not copy a running RocksDB directory
directly. For an application-consistent manual backup, follow the upstream
[maintenance instructions](https://continuwuity.org/maintenance.html) to use its
backup engine or stop the server before copying its data. Restore to an isolated
volume and validate accounts, room history and media before relying on recovery;
do not run two federating servers with the same identity.

## Validation

Local validation covers rendered Flux/Helm manifests, a non-root container startup
with a read-only root filesystem, the health endpoint, client/server discovery, and the
one-time registration flow followed by closed registration. Sable validation
covers non-root startup with a read-only root filesystem, runtime homeserver
configuration, security headers and SPA routes.

After deployment, verify public TLS/DNS, both discovery documents, federation,
Sable login, encrypted messaging and device verification with the intended clients, media
uploads, and a Kopiur restore. The readiness check alone does not establish these.
From an off-cluster host, request
`https://matrix.<your-domain>/_matrix/federation/v1/version` and confirm HTTP 200.
If it returns 403, check the CrowdSec bouncer decision before troubleshooting
Continuwuity; do not disable the gateway policy globally.

## References

- [Upstream release and primary container registry](https://forgejo.ellis.link/continuwuation/continuwuity/releases/tag/v26.9.1)
- [Container deployment](https://continuwuity.org/deploying/docker.html)
- [Matrix discovery/delegation](https://continuwuity.org/guides/delegation.html)
- [KubeSearch deployment examples](https://kubesearch.dev/hr/ghcr.io-bjw-s-labs-charts-continuwuity)
- [Sable v2 web deployment](https://git.sable.moe/SableClient/sable-next/src/tag/v2.3.5/docs/web.md)
- [Sable installation](https://docs.sable.moe/installation/)
