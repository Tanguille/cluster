# App database onboarding — CNPG `Database` CR + managed roles

Apps in this repo get their PostgreSQL role and database **declaratively from CloudNativePG**, not
from an init-container. This replaced the older `ghcr.io/home-operations/postgres-init` pattern
(an `init-db` container that ran `CREATE ROLE` / `CREATE DATABASE` with superuser credentials at
pod startup).

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

Two Flux Kustomizations reconcile these, and the split is **structurally required**, not cosmetic:

- **`cloudnative-pg-cluster`** reconciles the `Cluster` *and* the role Secrets together. The Cluster
  references each Secret via `managed.roles[].passwordSecret`, and CNPG requires that Secret to be
  in the Cluster's namespace — co-locating them means the Cluster never points at a Secret that has
  not been applied yet.
- **`cloudnative-pg-databases`** (`dependsOn: cloudnative-pg-cluster`, `wait: false`) reconciles the
  `Database` CRs. `wait: false` because a CNPG `Database` CR exposes **no kstatus `Ready`
  condition** — folding it into the `wait: true` cluster Kustomization would hang the health gate.

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
regenerate it.

> **Role attributes are fully declarative.** CNPG reconciles every *unspecified* role attribute to
> its default (no `login`/`createdb`/`createrole`/`superuser`/`replication`/`bypassrls`). So
> `login: true` alone is faithful for a plain app role — but any non-default attribute on the live
> role must be declared or it is reset. Example: the `nextcloud` role has `CREATEDB`, so its managed
> role declares `createdb: true`.

## Onboarding a new app — the five touch points

1. Add `cloudnative-pg/cluster/roles/<app>.sops.yaml` — a `kubernetes.io/basic-auth` Secret named
   `<role>-db` with `username`/`password` (the app's existing DB credentials). SOPS-encrypt it.
2. Reference it in `cloudnative-pg/cluster/kustomization.yaml` (`- roles/<app>.sops.yaml`).
3. Add a `managed.roles[]` entry in `cluster.yaml` (`login: true`, `passwordSecret.name: <role>-db`,
   plus any non-default attribute the live role has).
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
- **Extensions.** Declare them by name on `Database.spec.extensions` (e.g. memini: `vchord`,
  `vector`; crowdsec: `vector`). No version pin → CNPG keeps the installed version.
- **YAML anchor trap.** Some app-template HelmReleases define the secret `envFrom` anchor
  (`&envFrom` / `&secret`) **on the `init-db` container** and alias it on the app container. Deleting
  the init-db block also deletes the anchor and breaks the alias — relocate an explicit `secretRef`
  onto the app container.
- **Init-container removal differs per chart.** app-template apps remove the `initContainers.init-db`
  map; gatus removes an `initContainers` list; grafana removes the list inside the grafana-operator
  CR; nextcloud removes `extraInitContainers`; crowdsec removes a whole `postRenderers` JSON6902
  patch (its chart has no native init-container field).
- **Keep vs. remove `INIT_POSTGRES_*` keys.** Most apps have separate runtime DB keys, so drop all
  five `INIT_POSTGRES_*`. A few reuse `INIT_POSTGRES_*` **at runtime** and must keep them, dropping
  only `INIT_POSTGRES_SUPER_PASS`: spoolman (`SPOOLMAN_DB_*`), gatus (storage config), nextcloud
  (`externalDatabase.existingSecret` + notify-push).

## Onboarded apps

| App | DB | Role | Host | Extensions | Live owner was |
|-----|----|------|------|------------|----------------|
| jellystat | jfstat | jellystat | pgbouncer-rw | – | `postgres` (ALTERed) |
| radarr | radarr | radarr | pgbouncer-rw | – | role |
| bazarr | bazarr | bazarr | pgbouncer-rw | – | role |
| sonarr | sonarr | sonarr | pgbouncer-rw | – | role |
| prowlarr | prowlarr | prowlarr | pgbouncer-rw | – | `postgres` (ALTERed) |
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
   `kustomize build … | kubectl apply -n database --dry-run=server -f -`.
3. Confirm every edited `*.sops.yaml` is re-encrypted (`ENC[…]`, no plaintext) before committing.
4. Confirm the basic-auth password equals the app's existing runtime password.
