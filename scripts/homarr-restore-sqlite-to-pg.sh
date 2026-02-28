#!/usr/bin/env bash
#
# Homarr SQLite to PostgreSQL restore
# Restores a Homarr SQLite backup into the cluster's Homarr PostgreSQL database.
#
# Run from a machine with kubectl and cluster access (e.g. k8s-management).
# Optional: psql and pgloader installed locally; otherwise uses transient pods.
#
# Usage:
#   ./scripts/homarr-restore-sqlite-to-pg.sh [path/to/homarr-backup-db.sqlite]
#   BACKUP_FILE=/path/to/backup.sqlite ./scripts/homarr-restore-sqlite-to-pg.sh
#
# Connection: Loads DB_URL from homarr-secret (db-url or INIT_POSTGRES_*).
# If Postgres is only reachable in-cluster, use port-forward in another terminal:
#   kubectl port-forward -n database svc/pgbouncer-rw 5433:5432
#   export DB_URL='postgres://user:pass@127.0.0.1:5433/dbname'
#
set -euo pipefail

# Configuration
HOMARR_NS="${HOMARR_NS:-default}"
DEPLOYMENT="homarr"
SECRET_NAME="homarr-secret"
BACKUP_FILE="${1:-${BACKUP_FILE:-$HOME/homarr-backup-db.sqlite}}"

read_secret() {
  local key="$1"
  kubectl get secret -n "$HOMARR_NS" "$SECRET_NAME" -o jsonpath="{.data.$key}" 2>/dev/null | base64 -d || true
}

load_db_url() {
  if [[ -n "${DB_URL:-}" ]]; then
    echo "🔑 Using DB_URL from environment"
    return 0
  fi
  echo "🔑 Resolving PostgreSQL connection from secret $SECRET_NAME..."
  local url
  url=$(read_secret "db-url")
  if [[ -n "$url" ]]; then
    DB_URL="$url"
    echo "📊 Using db-url from secret"
    return 0
  fi
  local host port db user pass
  host=$(read_secret "INIT_POSTGRES_HOST")
  port=$(read_secret "INIT_POSTGRES_PORT")
  db=$(read_secret "INIT_POSTGRES_DBNAME")
  user=$(read_secret "INIT_POSTGRES_USER")
  pass=$(read_secret "INIT_POSTGRES_PASS")
  [[ -z "$port" ]] && port="5432"
  if [[ -z "$host" || -z "$db" || -z "$user" || -z "$pass" ]]; then
    echo "❌ Could not get DB_URL from secret. Set DB_URL or ensure secret has db-url or INIT_POSTGRES_*"
    echo "   Example: export DB_URL='postgres://user:pass@host:5432/homarr_db'"
    exit 1
  fi
  DB_URL="postgres://${user}:${pass}@${host}:${port}/${db}"
  echo "📊 Built DB_URL from INIT_POSTGRES_* (host=$host, db=$db)"
}

check_backup() {
  if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    exit 1
  fi
  echo "📁 Backup: $BACKUP_FILE ($(ls -lh "$BACKUP_FILE" | awk '{print $5}'))"
}

scale_homarr() {
  local replicas="$1"
  echo "🛠️ Scaling Homarr to $replicas..."
  kubectl scale deployment -n "$HOMARR_NS" "$DEPLOYMENT" --replicas="$replicas"
  if [[ "$replicas" == "0" ]]; then
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$HOMARR_NS" --timeout=120s || true
  else
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$HOMARR_NS" --timeout=300s
    kubectl wait --for=condition=available deployment/"$DEPLOYMENT" -n "$HOMARR_NS" --timeout=300s || true
  fi
}

clear_tables_psql() {
  echo "🧽 Clearing current Homarr PostgreSQL tables..."
  psql "$DB_URL" -v ON_ERROR_STOP=1 -c "
DO \$\$
DECLARE r RECORD;
BEGIN
  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
  LOOP
    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
  END LOOP;
END \$\$;
"
  echo "✅ Tables cleared"
}

parse_db_url() {
  # Accept postgres:// or postgresql://; optional ?query string after database name
  local u="$1"
  if [[ "$u" =~ postgres(ql)?://([^:]+):([^@]+)@([^:]+):([0-9]+)/([^?]+) ]]; then
    user="${BASH_REMATCH[2]}"
    pass="${BASH_REMATCH[3]}"
    host="${BASH_REMATCH[4]}"
    port="${BASH_REMATCH[5]}"
    db="${BASH_REMATCH[6]}"
    return 0
  fi
  return 1
}

clear_tables_transient() {
  echo "🧽 Clearing current Homarr PostgreSQL tables (via transient pod)..."
  local host port user db pass
  if ! parse_db_url "$DB_URL"; then
    echo "❌ Could not parse DB_URL for transient psql (expected postgres:// or postgresql://user:pass@host:port/dbname)"
    exit 1
  fi
  local pod_name="homarr-psql-$(date +%s)"
  kubectl run -n "$HOMARR_NS" "$pod_name" --restart=Never --image=postgres:17 \
    --env="PGHOST=$host" --env="PGPORT=$port" --env="PGPASSWORD=$pass" \
    --command -- bash -c "psql -h \"\$PGHOST\" -p \"\$PGPORT\" -U \"$user\" -d \"$db\" -v ON_ERROR_STOP=1 <<'SQL'
DO \$\$
DECLARE r RECORD;
BEGIN
  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
  LOOP
    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
  END LOOP;
END \$\$;
SQL" || {
    kubectl logs -n "$HOMARR_NS" pod/"$pod_name" 2>/dev/null || true
    kubectl delete pod -n "$HOMARR_NS" "$pod_name" --ignore-not-found=true 2>/dev/null || true
    exit 1
  }
  kubectl delete pod -n "$HOMARR_NS" "$pod_name" --wait=true --ignore-not-found=true 2>/dev/null || true
  echo "✅ Tables cleared"
}

run_pgloader_local() {
  echo "🔄 Running pgloader (local)..."
  pgloader --type sqlite \
    --with "include drop" \
    --with "create tables" \
    --with "reset sequences" \
    "$BACKUP_FILE" \
    "$DB_URL"
  echo "✅ pgloader completed"
}

run_pgloader_transient() {
  echo "🔄 Running pgloader (transient pod)..."
  local host port user db pass
  if ! parse_db_url "$DB_URL"; then
    echo "❌ Could not parse DB_URL for pgloader"
    exit 1
  fi
  local pod_name="homarr-pgloader-$(date +%s)"
  kubectl run -n "$HOMARR_NS" "$pod_name" --restart=Never --image=ghcr.io/roxedus/pgloader --command -- sleep 3600
  kubectl wait --for=condition=ready pod/"$pod_name" -n "$HOMARR_NS" --timeout=120s
  kubectl cp -n "$HOMARR_NS" "$BACKUP_FILE" "$pod_name:/tmp/homarr.db.backup"
  kubectl exec -n "$HOMARR_NS" "$pod_name" -- env \
    PGHOST="$host" PGPORT="$port" PGUSER="$user" PGPASSWORD="$pass" PGDATABASE="$db" \
    bash -c 'pgloader --type sqlite --with "include drop" --with "create tables" --with "reset sequences" \
      /tmp/homarr.db.backup "postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$PGDATABASE"'
  local rc=$?
  kubectl delete pod -n "$HOMARR_NS" "$pod_name" --wait=true --ignore-not-found=true 2>/dev/null || true
  if [[ $rc -ne 0 ]]; then
    echo "❌ pgloader failed (exit $rc)"
    exit $rc
  fi
  echo "✅ pgloader completed"
}

orchestrate() {
  echo "🚀 Homarr SQLite → PostgreSQL restore"
  echo "======================================"

  load_db_url
  check_backup

  if ! command -v kubectl &>/dev/null; then
    echo "❌ kubectl is required"
    exit 1
  fi

  scale_homarr 0

  if command -v psql &>/dev/null; then
    clear_tables_psql
  else
    clear_tables_transient
  fi

  if command -v pgloader &>/dev/null; then
    run_pgloader_local
  else
    run_pgloader_transient
  fi

  scale_homarr 1

  echo "🎉 Restore completed. Verify the Homarr UI:"
  echo "   kubectl logs -n $HOMARR_NS deployment/$DEPLOYMENT -f"
}

# Repair DB after pgloader: (1) __drizzle_migrations row + sequence, (2) missing integration.app_id column.
fix_post_restore() {
  echo "🔧 Fixing post-restore database..."
  load_db_url
  if ! command -v kubectl &>/dev/null; then
    echo "❌ kubectl is required"; exit 1
  fi
  if ! parse_db_url "$DB_URL"; then
    echo "❌ Could not parse DB_URL"; exit 1
  fi
  # 1) __drizzle_migrations: insert row from SQLite backup and fix sequence
  local hash="c6fd51d50bbe0a63eaab178e83dc81b202a9b6eb5fe714abfb8a1c19a45b7a44"
  local created_at="1715334238443"
  local sql="
INSERT INTO __drizzle_migrations (id, hash, created_at)
VALUES (1, '$hash', $created_at)
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('__drizzle_migrations', 'id'), (SELECT COALESCE(MAX(id), 1) FROM __drizzle_migrations));

-- 2) integration.app_id: pgloader/SQLite may have created table without this column (Homarr expects it)
ALTER TABLE integration ADD COLUMN IF NOT EXISTS app_id integer REFERENCES \"app\"(id);
"
  if command -v psql &>/dev/null; then
    psql "$DB_URL" -v ON_ERROR_STOP=1 -c "$sql"
  else
    local pod_name="homarr-psql-fix-$(date +%s)"
    local sql_line="INSERT INTO __drizzle_migrations (id, hash, created_at) VALUES (1, '$hash', $created_at) ON CONFLICT (id) DO NOTHING; SELECT setval(pg_get_serial_sequence('__drizzle_migrations', 'id'), (SELECT COALESCE(MAX(id), 1) FROM __drizzle_migrations)); ALTER TABLE integration ADD COLUMN IF NOT EXISTS app_id integer REFERENCES \"app\"(id);"
    kubectl run -n "$HOMARR_NS" "$pod_name" --restart=Never --image=postgres:17 \
      --env="PGHOST=$host" --env="PGPORT=$port" --env="PGPASSWORD=$pass" \
      --command -- bash -c "psql -h \"\$PGHOST\" -p \"\$PGPORT\" -U \"$user\" -d \"$db\" -v ON_ERROR_STOP=1 -c \"$sql_line\""
    local rc=$?
    kubectl delete pod -n "$HOMARR_NS" "$pod_name" --wait=true --ignore-not-found=true 2>/dev/null || true
    [[ $rc -ne 0 ]] && exit $rc
  fi
  echo "✅ DB fixed (__drizzle_migrations + integration.app_id). Restart Homarr: kubectl rollout restart deployment/$DEPLOYMENT -n $HOMARR_NS"
}

usage() {
  cat <<USAGE
Usage: $0 [path/to/homarr-backup-db.sqlite]
       $0 fix    Repair DB after restore (__drizzle_migrations row + sequence)

Restores a Homarr SQLite backup into the cluster PostgreSQL database.
Defaults to \$HOME/homarr-backup-db.sqlite or BACKUP_FILE env.

Connection: Loaded from secret $SECRET_NAME (db-url or INIT_POSTGRES_*).
Override: export DB_URL='postgres://user:pass@host:5432/dbname'

If Postgres is only in-cluster, in another terminal:
  kubectl port-forward -n database svc/pgbouncer-rw 5433:5432
  export DB_URL='postgres://user:pass@127.0.0.1:5433/dbname'

Requires: kubectl. Optional: psql + pgloader (otherwise uses transient pods).
USAGE
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  fix)       fix_post_restore; exit 0 ;;
  "")        orchestrate; exit 0 ;;
  *)         BACKUP_FILE="$1"; orchestrate; exit 0 ;;
esac
