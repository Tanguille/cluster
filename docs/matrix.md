# Matrix (Continuwuity and Sable)

Continuwuity serves `matrix.${SECRET_DOMAIN}` through `envoy-external`, with
federation on port 443. Account ids look like `@alice:matrix.example.com`.
Confirm this identity before first deployment: changing the server name later
requires a new database. Keep the hostname free of browser-only logins or
challenges, because Matrix clients and remote servers must reach it directly.

Sable is the web client at `https://sable.${SECRET_DOMAIN}`. It is stateless;
client state and encryption keys stay in the browser, so set up recovery and
keep the recovery key before clearing browser storage or moving devices.

## First administrator

1. Read the first-run registration token from the logs. Treat it as a credential:

   ```bash
   mise exec -- kubectl -n default logs deployment/continuwuity
   ```

2. In a Matrix client that supports registration tokens, such as Sable, select
   `https://matrix.<your-domain>` and register the administrator account. The
   first account becomes administrator and closes registration.
3. Create further accounts in the admin room with `!admin users create-user`.

## Storage and recovery

Kopiur snapshots the 5 GiB `/data` volume hourly. Increase `PVC_CAPACITY` in
`ks.yaml` as media grows.

Snapshots are crash-consistent, so recent writes may be missing. Do not copy a
running RocksDB directory. For a consistent manual backup, follow the upstream
[maintenance instructions](https://continuwuity.org/maintenance.html). Restore
to an isolated volume and validate accounts, room history and media first; never
run two federating servers with the same identity.

## After deployment

Verify public TLS/DNS, both `/.well-known/matrix/` documents, federation, Sable
login, encrypted messaging, media uploads and a Kopiur restore.

From an off-cluster host, request
`https://matrix.<your-domain>/_matrix/federation/v1/version` and confirm HTTP 200.
On a 403, check the CrowdSec decision first; do not disable the gateway policy
globally.

## References

- [Container deployment](https://continuwuity.org/deploying/docker.html)
- [Sable v2 web deployment](https://git.sable.moe/SableClient/sable-next/src/tag/v2.3.5/docs/web.md)
