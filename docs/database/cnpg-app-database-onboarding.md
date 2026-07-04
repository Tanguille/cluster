# Onboarding an app database on `postgres16`

Two patterns exist today; new apps should use the CNPG-native one.

## CNPG-native (use this for new apps)

1. Add a role in `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` under `spec.managed.roles`, with `passwordSecret` pointing at a SOPS-encrypted `basic-auth` Secret (see `cluster/secret.sops.yaml`).
2. Add a `Database` CR in `kubernetes/apps/database/cloudnative-pg/databases/<app>-database.yaml`, referencing `cluster.name: postgres16` and the role as `owner`. Set `databaseReclaimPolicy: retain` unless the data is genuinely disposable.
3. Build the app's `DATABASE_URL` from the *same* password used in step 1 — the role and the app's connection string are two independent secrets today, nothing resyncs them if one changes. If CNPG ever rotates the role's password, update the app's secret to match by hand (see `litellm` for the current example).

This replaces the `ghcr.io/home-operations/postgres-init` initContainer pattern: no more bootstrap container, no imperative `CREATE DATABASE`/`CREATE ROLE` on every pod start.

## `postgres-init` (legacy, still used by most existing apps)

`nextcloud`, `bazarr`, `prowlarr`, `radarr`, `sonarr`, `memini`, `spoolman`, `jellystat`, and others still bootstrap their database via a `postgres-init` initContainer. Not yet migrated — that's tracked separately, not a per-app decision to make ad hoc.
