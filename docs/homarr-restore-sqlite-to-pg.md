# Homarr restore: SQLite backup to PostgreSQL

Restore a Homarr SQLite backup into the cluster’s Homarr PostgreSQL database. To **start from scratch** (no SQLite, fresh Postgres schema and onboarding), see [Option D: Start from scratch](#option-d-start-from-scratch-no-sqlite).

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

## 5. Login or password reset doesn’t work

After a SQLite→Postgres restore, login can fail and in-app password reset may not fix it. Common causes: password hashes or session data were migrated with wrong encoding/type by pgloader, or NextAuth session state is inconsistent.

### Option A: Force password reset via Homarr CLI (recommended first)

Use Homarr’s built-in recovery tool so it writes a **new** password hash to Postgres (bypassing any corrupted migrated hash). Run inside the Homarr pod:

```bash
# Replace YOUR_USERNAME with the exact username you used to log in
kubectl exec -n default deployment/homarr -it -- homarr reset-owner-password --username YOUR_USERNAME
```

The command prints a new temporary password; use it to log in and then change it in the UI. If you don’t remember the username, inspect the DB (see Option B) or try the first admin username you used in the old instance.

If the CLI fails (e.g. “user not found” or DB error), the `user` table may be missing or corrupted—proceed to Option B or C.

### Option B: Inspect the database

Connect to the Homarr Postgres DB (see step 1–2 for `DB_URL` / port-forward), then:

```bash
psql "$DB_URL" -c "\dt"                    # list tables
psql "$DB_URL" -c "SELECT id, name FROM \"user\";"   # list users (Homarr uses "user" table)
```

If the `user` table is empty or missing, auth was not migrated correctly. If it has rows but login still fails after Option A, the issue may be session/cookie or NextAuth config (e.g. `NEXTAUTH_URL`), not the hash.

### Option C: Fresh auth only (keep boards, lose users)

If you want to keep boards/widgets but are okay recreating users:

1. Connect to the Homarr DB and **delete only auth-related data** (exact table names may vary by Homarr version; typical NextAuth/Drizzle names):
   - `session`, `account`, and optionally `user` (or only clear `session`/`account` and then use CLI to reset the remaining user’s password).
2. Restart Homarr and go through onboarding again if the app treats the instance as “no users” (e.g. create a new admin). Existing boards may still be in other tables.

Only do this if you’re comfortable with SQL and have a backup; wrong deletes can break the app.

### Option D: Start from scratch (no SQLite)

If nothing above works or you prefer a clean state: wipe the **PostgreSQL** Homarr database only (no SQLite backup involved). Homarr will recreate the schema on startup; you complete onboarding and create a new admin.

1. **Get `DB_URL`** (same as step 1–2 above; port-forward if needed).
2. **Run the reset** (Bash script; drops all tables in the Homarr Postgres database, then scales Homarr back up):

   ```bash
   cd /path/to/cluster
   export DB_URL='postgres://...'   # from step 1
   ./scripts/homarr-restore-sqlite-to-pg.sh reset
   ```

3. **Open Homarr** and complete onboarding (create first admin). All previous users and data from any SQLite backup are gone; the app uses a fresh Postgres schema.

The script uses the same connection and table-dropping logic as the restore path (PostgreSQL `pg_tables` + `quote_ident` for safe identifiers); only the pgloader/SQLite step is omitted.

## Encryption key

Do not change `db-encryption-key` in the Homarr secret when restoring. The backup was created with a specific key; the same key must be used so encrypted fields decrypt correctly.
