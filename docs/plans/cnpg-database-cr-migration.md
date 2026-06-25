# CNPG-native DB onboarding migration (postgres-init → Database CR + managed roles)

Replace the `ghcr.io/home-operations/postgres-init` init-container pattern in every app
with CloudNativePG-native onboarding: a `spec.managed.roles[]` entry on the `postgres16`
Cluster (adopts the existing role) + a `Database` CR in the `database` namespace (adopts the
existing database, `ensure: present`, `databaseReclaimPolicy: retain`).

Branch: `feat/cnpg-database-cr-migration` (worktree). One commit per app.

> **Status (2026-06-26): 12/13 migrated and committed** (jellystat, radarr, bazarr, sonarr,
> prowlarr, open-webui, memini, spoolman, gatus, nextcloud, grafana, crowdsec). **immich is
> deferred** — see the dedicated section. Validated via `kustomize build` + server-side
> dry-run of the full Cluster and all 12 Database CRs. Not yet pushed/applied.

## Architecture decisions

- **Role password Secrets** (`kubernetes.io/basic-auth`, `<app>-db`) live in
  `cloudnative-pg/cluster/roles/` and are reconciled by the existing `cloudnative-pg-cluster`
  Flux Kustomization — i.e. the *same* Kustomization as the `Cluster` that references them, so
  the Cluster never points at a Secret that has not been applied yet.
- **Database CRs** live in `cloudnative-pg/databases/` reconciled by a new
  `cloudnative-pg-databases` Flux Kustomization (`dependsOn: cloudnative-pg-cluster`,
  `wait: false`). `wait:false` because a CNPG `Database` CR exposes no kstatus `Ready`
  condition; folding it into the `wait:true` cluster Kustomization risks a stuck health gate.
- **Apps keep their existing `dependsOn: cloudnative-pg-cluster`** — unchanged. Safe because
  every database is *adopted* (already exists), so there is no create-then-wait race.
- **Passwords are reused verbatim** (`Optimist` everywhere in this homelab). CNPG *enforces*
  the managed-role password, so the basic-auth Secret value must equal the app's existing
  runtime credential or the app is locked out.

## Secret-consumption patterns (decides which keys to remove)

- **REMOVE-ALL** (app has separate runtime keys → drop all 5 `INIT_POSTGRES_*`):
  radarr, bazarr, sonarr, prowlarr, open-webui, memini, crowdsec, grafana.
- **KEEP** (app reuses `INIT_POSTGRES_*` at *runtime* → drop only `INIT_POSTGRES_SUPER_PASS`):
  spoolman (`SPOOLMAN_DB_*` ← `INIT_*`), gatus (`config.yaml` storage path ← `INIT_*`),
  nextcloud (`externalDatabase.existingSecret` + notify-push ← `INIT_POSTGRES_USER/PASS`).

> **YAML anchor trap:** radarr, bazarr, sonarr, prowlarr, open-webui, memini, grafana define
> the secret `envFrom` anchor (`&envFrom` / `&secret`) **on the init-db container** and alias
> it on the app container. Deleting the init-db block also deletes the anchor → the alias
> breaks. When removing init-db, relocate the explicit `secretRef` onto the app container.

## Per-app inventory

| App | DB | User | Owner (live) | Host | Ext | Pattern | Init-db location | Status |
|-----|----|------|--------------|------|-----|---------|------------------|--------|
| jellystat | jfstat | jellystat | **postgres → jellystat** | pgbouncer-rw | – | remove-all | controller initContainers | ✅ done |
| radarr | radarr | radarr | radarr | pgbouncer-rw | – | remove-all (anchor) | controller initContainers | ☐ |
| bazarr | bazarr | bazarr | bazarr | pgbouncer-rw | – | remove-all (anchor) | controller initContainers | ☐ |
| sonarr | sonarr | sonarr | sonarr | pgbouncer-rw | – | remove-all (anchor) | controller initContainers | ☐ |
| prowlarr | prowlarr | prowlarr | **postgres → prowlarr** | pgbouncer-rw | – | remove-all (anchor) | controller initContainers | ☐ |
| gatus | gatus | gatus | gatus | pgbouncer-rw | – | keep | initContainers list | ☐ |
| grafana | grafana | grafana | grafana | pgbouncer-session | – | remove-all (anchor) | instance/grafana.yaml | ☐ |
| nextcloud | nextcloud | nextcloud | **postgres → nextcloud** | pgbouncer-rw | – | keep (app + notify-push) | app initContainers | ☐ |
| spoolman | spoolman | spoolman | spoolman | pgbouncer-rw | – | keep | controller initContainers | ☐ |
| open-webui | openwebui | openwebui | openwebui | pgbouncer-rw | – | remove-all (anchor) | controller initContainers | ☐ |
| memini | memini | memini | memini | pgbouncer-rw | **vchord, vector** | remove-all (anchor) | controller initContainers | ☐ |
| crowdsec | crowdsec | crowdsec | crowdsec | pgbouncer-rw | **vector** | remove-all | postRenderers patch | ☐ |
| immich | immich (MISSING) | immich | — (only orphan `app`) | pgbouncer-session | pgvector wanted | **DEFERRED** | controller initContainers | ⚠ see below |

## ⚠ immich — excluded, needs a human decision

immich's `DB_URL` targets database `immich`, which **does not exist** in the cluster (only an
orphan `app` db owned by role `app`, with no vector extension), and immich has **no live DB
connections** (not running). A `Database` CR with `ensure: present` would *create a new empty
`immich` db*, not adopt data. Decide first: decommission immich, point it at `app`, or restore
`immich` from backup — then migrate. Do **not** run the mechanical pattern on it.

## TODO / follow-ups

- [ ] **owner ALTER verification (jfstat, nextcloud, prowlarr).** These 3 are owned by
  `postgres`; the `Database` CR `owner:` triggers a one-time `ALTER DATABASE … OWNER TO <role>`
  when CNPG reconciles. This is idempotent — once converged the state is clean and CNPG does
  **not** re-issue it, and there is nothing to remove from the manifests (`owner:` is required
  and stays as declarative state). After apply, verify each converged:
  `kubectl -n database exec postgres16-<primary> -c postgres -- psql -U postgres -tAc \
   "SELECT datname, pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname IN ('jfstat','nextcloud','prowlarr');"`
  and confirm no repeated `ALTER DATABASE … OWNER` entries in the CNPG operator logs.
- [ ] Resolve immich (above).
- [ ] Optional: once no app needs superuser onboarding, reconsider `enableSuperuserAccess: true`.

## Validation per app

1. `kustomize build` the touched app dir + `cloudnative-pg/cluster` + `cloudnative-pg/databases`.
2. Server dry-run the Database CR + Cluster: `kubectl apply -n database --dry-run=server -f -`.
3. SOPS: every edited `*.sops.yaml` re-encrypted (`ENC[…]`, no plaintext secret), via `sops -e -i`.
4. Confirm the basic-auth password == the app's existing runtime password.

## Process instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been
  consolidated into existing documentation, the plan file can be removed. If there is no relevant
  existing documentation, the plan should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
