# Homarr restore: SQLite backup to PostgreSQL

Restore a Homarr SQLite backup into the cluster’s Homarr PostgreSQL database.

## Prerequisites

- `kubectl` (cluster access)
- `psql` (PostgreSQL client)
- `pgloader` (e.g. `apt install pgloader` on Debian)
- SQLite backup file (e.g. `~/homarr-backup-db.sqlite`)
- Homarr Postgres connection URL (`DB_URL`)

## 1. Get Postgres connection URL

You need a `postgres://USER:PASS@HOST:PORT/DATABASE` URL for the **Homarr** database.

**From the cluster (no decryption):**

```bash
kubectl get secret homarr-secret -n default -o jsonpath='{.data.db-url}' | base64 -d
```

If the secret is only in Git (SOPS), decrypt and read:

```bash
cd /path/to/cluster
sops -d kubernetes/apps/default/homarr/app/secret.sops.yaml | yq '.stringData["db-url"]'
```

Or build the URL from init vars: decrypt the secret and use `INIT_POSTGRES_HOST`, `INIT_POSTGRES_DBNAME`, `INIT_POSTGRES_USER`, `INIT_POSTGRES_PASS` →
`postgres://USER:PASS@HOST:5432/DATABASE`.

## 2. Port-forward (if Postgres is only in-cluster)

From a machine that can reach the cluster:

```bash
kubectl port-forward -n database svc/pgbouncer-rw 5433:5432
```

Use in `DB_URL`: `postgres://USER:PASS@127.0.0.1:5433/DATABASE`.

## 3. Run the restore script

Run from a machine that has the backup file, `kubectl`, `psql`, and `pgloader` (e.g. **k8s-management**):

```bash
cd /path/to/cluster
export DB_URL='postgres://...'   # from step 1 (use 127.0.0.1:5433 if using port-forward)
./scripts/homarr-restore-sqlite-to-pg.sh /path/to/homarr-backup-db.sqlite
```

Default backup path if omitted: `$HOME/homarr-backup-db.sqlite`.

The script will:

1. Scale Homarr to 0
2. Drop all tables in the Homarr database
3. Load the SQLite backup into Postgres with pgloader
4. Scale Homarr back to 1

## 4. Verify and fix (if “corrupted”)

- Open the Homarr UI and check boards/widgets.
- Check logs: `kubectl logs -n default deployment/homarr -f`.

If Homarr reports DB errors or “corrupted” after restore, pgloader may have left `__drizzle_migrations` empty (null `id` row skipped). Repair and restart:

```bash
./scripts/homarr-restore-sqlite-to-pg.sh fix
kubectl rollout restart deployment/homarr -n default
```

## Encryption key

Do not change `db-encryption-key` in the Homarr secret when restoring. The backup was created with a specific key; the same key must be used so encrypted fields decrypt correctly.
