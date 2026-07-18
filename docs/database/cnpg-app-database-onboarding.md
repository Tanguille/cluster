# App database onboarding — CNPG `Database` CR + managed roles

Apps in this repo get their PostgreSQL role and database **declaratively from CloudNativePG**, not
from an init-container. This replaced the `ghcr.io/home-operations/postgres-init` pattern (an
`init-db` container that ran `CREATE ROLE` / `CREATE DATABASE` with superuser credentials at pod
startup). Only `immich` still uses it, deliberately deferred — see "Not onboarded" below.

Each onboarded app has:

- a **`spec.managed.roles[]`** entry on the `postgres16` `Cluster` — declares the login role, with
  its password sourced from a per-app Secret, and
- a **`Database`** CR in the `database` namespace — declares the database, its owner, and any
  extensions.

The app's runtime connection string (`DATABASE_URL` / `POSTGRES_*`) is unchanged; it still connects
through the relevant pgbouncer pooler.

## Layout

| Path | What |
|------|------|
| `cloudnative-pg/cluster/cluster.yaml` → `spec.managed.roles[]` | One entry per app's login role |
| `cloudnative-pg/cluster/roles/<app>.sops.yaml` | `kubernetes.io/basic-auth` Secret (`<role>-db`) holding the role password |
| `cloudnative-pg/databases/<app>.yaml` | The `Database` CR |
| `cloudnative-pg/databases/kustomization.yaml` | Lists the `Database` CRs |

Two Flux Kustomizations reconcile these:

- **`cloudnative-pg-cluster`** reconciles the `Cluster` *and* the role Secrets together. The Cluster
  references each Secret via `managed.roles[].passwordSecret`, and CNPG requires that Secret to be
  in the Cluster's namespace — co-locating them means the Cluster never points at a Secret that has
  not been applied yet.
> **Why these aren't co-located with each app.** `Database.spec.cluster` and
> `managed.roles[].passwordSecret` are Kubernetes `LocalObjectReference`s — the schema has no
> namespace field, so CNPG always resolves the Cluster/Secret *in the same namespace as the object
> doing the referencing*. Since `postgres16` lives in `database`, every `Database` CR and role
> Secret must too; there is no way to place them in an app's own namespace and have them work.
> This is a known, tracked upstream limitation:
> [cloudnative-pg/cloudnative-pg#6043](https://github.com/cloudnative-pg/cloudnative-pg/issues/6043)
> ("allow cross-namespace Database and Role configuration"), open and unimplemented as of
> 2026-07-11. If/when that lands, revisit moving `cluster/roles/<app>.sops.yaml` and
> `databases/<app>.yaml` into each app's own directory — until then, the shared-cluster model here
> requires the centralized layout below.

- **`cloudnative-pg-databases`** (`dependsOn: cloudnative-pg-cluster`, `wait: true`) reconciles the
  `Database` CRs. Kept as a separate Kustomization so a single DB-provisioning failure can't stall
  the shared, `wait: true` cluster Kustomization and cascade to every app depending on it.

Apps keep their existing `dependsOn: cloudnative-pg-cluster`.

## Adoption vs. creation

These resources were introduced to **adopt** databases/roles that already existed (created by the
old init-container). Two settings make adoption safe, and both are kept **even though they equal the
CRD default** — they are the load-bearing data-safety signals:

- **`Database.spec.ensure: present`** — ensure the database/role exists; never `absent`.
- **`Database.spec.databaseReclaimPolicy: retain`** — if the CR is deleted or pruned, CNPG must
  **not** drop the underlying database.

CNPG **enforces** the managed-role password, so the basic-auth Secret value MUST equal the app's
existing runtime credential — otherwise the app is locked out. Reuse the existing password; never
regenerate it. CNPG can rotate a managed role's password, but nothing here re-syncs it into the
app's runtime secret — if rotation is ever enabled for these roles, add a mechanism (e.g.
Reloader-driven resync) to keep the two in step. If CNPG does rotate a password, update the app's
secret by hand to match.

> **Role attributes are fully declarative.** CNPG reconciles every *unspecified* role attribute to
> its default (no `login`/`createdb`/`createrole`/`superuser`/`replication`/`bypassrls`). So
> `login: true` alone is faithful for a plain app role — but any non-default attribute on the live
> role must be declared or it is reset. Example: the `nextcloud` role has `CREATEDB`, so its managed
> role declares `createdb: true`.

## Onboarding a new app — the five touch points

1. Add `cloudnative-pg/cluster/roles/<app>.sops.yaml` — a `kubernetes.io/basic-auth` Secret named
   `<role>-db` with `username`/`password` (the app's existing DB credentials), and label
   `cnpg.io/reload: "true"`. SOPS-encrypt it.
2. Reference it in `cloudnative-pg/cluster/kustomization.yaml` (`- roles/<app>.sops.yaml`).
3. Add a `managed.roles[]` entry in `cluster.yaml` (`ensure: present`, `login: true`,
   `passwordSecret.name: <role>-db`, plus any non-default attribute the live role has).
4. Add `cloudnative-pg/databases/<app>.yaml` — the `Database` CR (`owner: <role>`,
   `databaseReclaimPolicy: retain`, and `extensions:` if the DB uses any).
5. Reference it in `cloudnative-pg/databases/kustomization.yaml`.

Then remove the app's `init-db` container and its `INIT_POSTGRES_*` Secret keys (see gotchas below).

### Gotchas

- **Owner ALTER.** If the live database is owned by `postgres` rather than the app role, the
  `Database` CR's `owner:` triggers a one-time, idempotent `ALTER DATABASE … OWNER TO <role>` on
  first reconcile, then converges. `owner:` is required and stays as declarative state — there is
  nothing to remove afterward. After first apply, confirm convergence:
  ```
  kubectl -n database exec postgres16-<primary> -c postgres -- psql -U postgres -tAc \
    "SELECT datname, pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = '<db>';"
  ```
- **Extensions.** Declare them by name and version on `Database.spec.extensions` (for example,
  memini uses `vector` `0.8.5` and `vchord` `1.1.1`; crowdsec uses `vector` with its matching
  target). Every `spec.extensions[].version` MUST align with the pinned extension-bearing image.
  The version-compatibility guard enforces this alignment and rejects a change that updates the
  image without updating the declared target. Read the `vector` target from the exact pinned
  operand image used by the cluster; do not blindly copy it from an upstream vector release,
  which may not be packaged in that image.
- **YAML anchor trap.** Some app-template HelmReleases define the secret `envFrom` anchor
  (`&envFrom` / `&secret`) **on the `init-db` container** and alias it on the app container. Deleting
  the init-db block also deletes the anchor and breaks the alias — relocate an explicit `secretRef`
  onto the app container.
- **Init-container removal differs per chart.** app-template apps remove the `initContainers.init-db`
  map; gatus removes a top-level `initContainers` list; grafana removes the list inside the
  grafana-operator CR's pod template; nextcloud removes `extraInitContainers`; crowdsec removes the
  `lapi.extraInitContainers` list (a native chart field — not a postRenderers patch).
- **Keep vs. remove `INIT_POSTGRES_*` keys.** Most apps have separate runtime DB keys, so drop all
  five `INIT_POSTGRES_*`. A few reuse `INIT_POSTGRES_*` **at runtime** and must keep them, dropping
  only `INIT_POSTGRES_SUPER_PASS`: spoolman (`SPOOLMAN_DB_*`), gatus (storage config), nextcloud
  (`externalDatabase.existingSecret` + notify-push).

## Onboarded apps

| App | DB | Role | Host | Extensions | Live owner was |
|-----|----|------|------|------------|----------------|
| litellm | litellm | litellm | pgbouncer-rw | – | role |
| jellystat | jfstat | jellystat | pgbouncer-rw | – | `postgres` (ALTERed) |
| radarr | radarr | radarr | pgbouncer-rw | – | role |
| bazarr | bazarr | bazarr | pgbouncer-rw | – | role |
| sonarr | sonarr | sonarr | pgbouncer-rw | – | role |
| prowlarr | prowlarr | prowlarr | pgbouncer-rw | – | role |
| gatus | gatus | gatus | pgbouncer-rw | – | role |
| grafana | grafana | grafana | pgbouncer-session | – | role |
| nextcloud | nextcloud | nextcloud | pgbouncer-rw | – | `postgres` (ALTERed); role has `CREATEDB` |
| spoolman | spoolman | spoolman | pgbouncer-rw | – | role |
| memini | memini | memini | pgbouncer-rw | vchord, vector | role |
| crowdsec | crowdsec | crowdsec | pgbouncer-rw | vector | role |

## Not onboarded

- **immich** — its `DB_URL` targets a database `immich` that does **not** exist (only an orphan `app`
  db, no vector extension), and immich is not running. A `Database` CR with `ensure: present` would
  create a new empty `immich`, not adopt data. Decide first — decommission, repoint to `app`, or
  restore `immich` from backup — then onboard. Its init-db is intentionally left in place.

## Validation before applying

1. `kustomize build` the touched app dir + `cloudnative-pg/cluster` + `cloudnative-pg/databases`.
2. Server-side dry-run against the live CNPG admission webhook:
   `kustomize build … | kubectl apply --dry-run=server -f -`.
3. Confirm every edited `*.sops.yaml` is re-encrypted (`ENC[…]`, no plaintext) before committing.
4. Confirm the basic-auth password equals the app's existing runtime password.
