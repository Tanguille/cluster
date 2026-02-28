# Nextcloud PostgreSQL read replica

Nextcloud uses a read-only PgBouncer pooler (`pgbouncer-ro`) for read queries and the existing read-write pooler (`pgbouncer-rw`) for writes, via `dbreplica` (Nextcloud 29+).

## How it’s configured (same pattern as main DB)

Like the main database connection, credentials stay in the secret: the chart does not get user/password from Helm values but from `existingSecret` (secretName + usernameKey/passwordKey). For the replica there is no chart option, so the **entire replica config snippet** is stored in the same secret under the key `replica.config.php` and mounted as a file at `/var/www/html/config/replica.config.php`. No init container, no Flux substitution, no extra Kustomizations.

**One-time setup:** The secret ships with placeholders in `replica.config.php`. Decrypt the secret, replace `REPLACE_WITH_INIT_POSTGRES_DBNAME`, `REPLACE_WITH_INIT_POSTGRES_USER`, and `REPLACE_WITH_INIT_POSTGRES_PASS` with the values of `INIT_POSTGRES_DBNAME`, `INIT_POSTGRES_USER`, and `INIT_POSTGRES_PASS` from the same file, then re-encrypt (`sops -e -i kubernetes/apps/default/nextcloud/app/secret.sops.yaml`). Restart Nextcloud so it picks up the config: `kubectl rollout restart deployment/nextcloud -n default`.

## Infrastructure

- **Read-write:** `pgbouncer-rw.database.svc.cluster.local:5432` (Pooler in `kubernetes/apps/database/cloudnative-pg/cluster/pooler.yaml`)
- **Read-only:** `pgbouncer-ro.database.svc.cluster.local:5432` (Pooler in `kubernetes/apps/database/cloudnative-pg/cluster/pooler-ro.yaml`)

Both poolers use the same CNPG cluster (`postgres16`); the RO pooler routes to replicas.

## Verifying Nextcloud is using RO connections

Any session you see when connected **to a replica** is a read-only connection. Use the CloudNative-PG plugin to open psql on a replica and inspect `pg_stat_activity`.

1. **Connect to a replica with the cnpg plugin** (uses the postgres superuser; no port-forward or local psql needed):

   ```bash
   kubectl cnpg psql --replica postgres16 -n database
   ```

2. **In the psql session**, switch to the Nextcloud database, then run the query (run `\c nextcloud` first and press Enter; run the `SELECT` separately):

   ```sql
   \c nextcloud
   ```

   ```sql
   SELECT usename, application_name, client_addr, state
   FROM pg_stat_activity
   WHERE datname = 'nextcloud';
   ```

   Every row is a connection **on that replica** (read-only). You should see at least your own session.

3. **Generate read load in Nextcloud** (open Files, dashboard, browse), then run the same `SELECT` again. If the replica is in use, you should see more connections (e.g. from the pooler or the nextcloud user). Those are the read connections offloaded to the replica.

4. **Optional: compare with the primary**
   Run the same query on the primary: `kubectl cnpg psql postgres16 -n database` (no `--replica`). Then `\c nextcloud` and the same `pg_stat_activity` query. You’ll see connections that use the primary (writes and causal reads). With RO in use, a good share of read traffic should show up on the replica (step 1), not only on the primary.

**Alternative (port-forward + psql):** If you prefer not to use the cnpg plugin, port-forward to `pgbouncer-ro` and connect with the Nextcloud DB user: `kubectl port-forward -n database svc/pgbouncer-ro 5434:5432`, then `psql "postgres://<user>:<pass>@127.0.0.1:5434/<dbname>"` and run the same `pg_stat_activity` query. Connections seen there are also on the replica (RO pooler targets replicas only).
