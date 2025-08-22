#!/bin/bash

# Radarr SQLite to PostgreSQL Migration Script
# This script automates the migration from SQLite to PostgreSQL
#
# Updated with comprehensive sequence resets and integrity fixes to resolve:
# - KeyNotFoundException errors after migration
# - Duplicate key constraint violations
# - Referential integrity issues between Movies, MovieFiles, and MovieMetadata
#
# Use inpod-* functions for modular execution within Kubernetes pods

set -e

# Configuration
NAMESPACE="media"
DEPLOYMENT="radarr"
BACKUP_DIR="/tmp/radarr-migration"

wait_for_pod_completion() {
  local pod_name="$1"
  local timeout_seconds="${2:-300}"
  local start_ts
  start_ts=$(date +%s)

  while true; do
    local phase
    phase=$(kubectl get pod "$pod_name" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")

    if [ "$phase" = "Succeeded" ]; then
      echo "✅ $pod_name completed successfully"
      return 0
    fi
    if [ "$phase" = "Failed" ]; then
      echo "❌ $pod_name failed"
      kubectl logs "$pod_name" -n "$NAMESPACE" || true
      return 1
    fi

    local now
    now=$(date +%s)
    if [ $(( now - start_ts )) -ge "$timeout_seconds" ]; then
      echo "⏰ Timeout waiting for $pod_name (last phase: $phase)"
      kubectl describe pod "$pod_name" -n "$NAMESPACE" || true
      return 1
    fi
    sleep 3
  done
}

delete_pod_if_exists() {
  local pod_name="$1"
  kubectl delete pod "$pod_name" -n "$NAMESPACE" --ignore-not-found=true >/dev/null 2>&1 || true
}

echo "🚀 Starting Radarr SQLite to PostgreSQL Migration"
echo "=================================================="

# Get PostgreSQL connection details from env or the secret
echo "🔑 Resolving PostgreSQL connection details (env or secret)..."

# Prefer Radarr v5 env format RADARR__POSTGRES__*, fallback to legacy POSTGRES_* keys
read_secret_val() {
  local key="$1"
  kubectl get secret -n "$NAMESPACE" radarr-secret -o jsonpath="{.data.$key}" 2>/dev/null | base64 -d || true
}

if [ -z "$SKIP_GLOBAL_PG_INIT" ]; then
  # If any required PG_* var is missing, try to load from Kubernetes secret
  if [ -z "$PG_HOST" ] || [ -z "$PG_PORT" ] || [ -z "$PG_DB" ] || [ -z "$PG_USER" ] || [ -z "$PG_PASSWORD" ]; then
    echo "ℹ️ Loading missing PostgreSQL details from Kubernetes secret..."
    PG_HOST=${PG_HOST:-$(read_secret_val RADARR__POSTGRES__HOST)}
    PG_PORT=${PG_PORT:-$(read_secret_val RADARR__POSTGRES__PORT)}
    PG_DB=${PG_DB:-$(read_secret_val RADARR__POSTGRES__MAINDB)}
    PG_USER=${PG_USER:-$(read_secret_val RADARR__POSTGRES__USER)}
    PG_PASSWORD=${PG_PASSWORD:-$(read_secret_val RADARR__POSTGRES__PASSWORD)}

    # Fallbacks for older/alt secret keys
    if [ -z "$PG_HOST" ]; then PG_HOST=$(read_secret_val INIT_POSTGRES_HOST); fi
    if [ -z "$PG_HOST" ]; then PG_HOST=$(read_secret_val POSTGRES_IP); fi
    if [ -z "$PG_PORT" ]; then PG_PORT=$(read_secret_val POSTGRES_PORT); fi
    if [ -z "$PG_DB" ]; then PG_DB=$(read_secret_val INIT_POSTGRES_DBNAME); fi
    if [ -z "$PG_DB" ]; then PG_DB=$(read_secret_val POSTGRES_DB); fi
    if [ -z "$PG_USER" ]; then PG_USER=$(read_secret_val POSTGRES_USER); fi
    if [ -z "$PG_PASSWORD" ]; then PG_PASSWORD=$(read_secret_val POSTGRES_PASSWORD); fi
  fi

  # Basic validation
  missing=()
  [ -z "$PG_HOST" ] && missing+=("host")
  [ -z "$PG_PORT" ] && missing+=("port")
  [ -z "$PG_DB" ] && missing+=("database")
  [ -z "$PG_USER" ] && missing+=("user")
  [ -z "$PG_PASSWORD" ] && missing+=("password")
  if [ ${#missing[@]} -ne 0 ]; then
    echo "❌ Missing PostgreSQL secret values: ${missing[*]}"
    echo "   Ensure your secret has RADARR__POSTGRES__HOST/PORT/USER/PASSWORD/MAINDB (per Servarr guide)."
    exit 1
  fi

  echo "📊 PostgreSQL connection details:"
  echo "  Host: $PG_HOST"
  echo "  Port: $PG_PORT"
  echo "  Database: $PG_DB"
  echo "  User: $PG_USER"
fi

# In-pod helpers (no kubectl required). Provide subcommands to run only what you need.

backup_sqlite_local() {
  local src_db="${SQLITE_DB_PATH:-/config/radarr.db}"
  local dst_db="${BACKUP_PATH:-/tmp/radarr.db.backup}"
  echo "💾 Copying SQLite database from ${src_db} to ${dst_db}..."
  if [ ! -f "$src_db" ]; then
    echo "❌ SQLite database not found at $src_db"; return 1
  fi
  cp -f "$src_db" "$dst_db"
  ls -lh "$dst_db" || true
}

preclean_tables() {
  echo "🧽 Pre-cleaning conflicting tables per Servarr guide..."
  PGPASSWORD=$PG_PASSWORD psql -h "$PG_HOST" -p "${PG_PORT:-5432}" -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=0 \
    -c 'DELETE FROM "QualityProfiles";' \
    -c 'DELETE FROM "QualityDefinitions";' \
    -c 'DELETE FROM "DelayProfiles";' \
    -c 'DELETE FROM "Metadata";' || true
}

run_pgloader_local() {
  local src_db="${BACKUP_PATH:-/tmp/radarr.db.backup}"
  if [ ! -f "$src_db" ]; then
    echo "ℹ️ No backup at $src_db, attempting to copy from /config..."
    backup_sqlite_local || return 1
  fi
  echo "🔄 Running pgloader..."
  pgloader --type sqlite \
    --with "quote identifiers" \
    --with "data only" \
    --with "prefetch rows = 100" \
    --with "batch size = 1MB" \
    --with "concurrency = 1" \
    "$src_db" \
    "postgresql://$PG_USER:$PG_PASSWORD@$PG_HOST:${PG_PORT:-5432}/$PG_DB"
}

integrity_fix_local() {
  echo "🧪 Fixing referential integrity (Movies <> MovieFiles/MovieMetadata)..."
  PGPASSWORD=$PG_PASSWORD psql -h "$PG_HOST" -p "${PG_PORT:-5432}" -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- Diagnostic counts before fix
SELECT COUNT(*) AS missing_metadata
FROM "Movies" m LEFT JOIN "MovieMetadata" mm ON mm."Id"=m."MovieMetadataId"
WHERE mm."Id" IS NULL;

SELECT COUNT(*) AS missing_moviefile
FROM "Movies" m LEFT JOIN "MovieFiles" mf ON mf."Id"=m."MovieFileId"
WHERE m."MovieFileId" IS NOT NULL AND mf."Id" IS NULL;

-- 1) Align MovieFiles.MovieId with Movies.Id for the file the movie references
UPDATE "MovieFiles" mf
SET "MovieId" = m."Id"
FROM "Movies" m
WHERE m."MovieFileId" = mf."Id"
  AND (mf."MovieId" IS DISTINCT FROM m."Id");

-- 2) Drop NOT NULL on Movies.MovieFileId if present (Radarr allows movies without a file)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'Movies'
      AND column_name = 'MovieFileId'
      AND is_nullable = 'NO'
  ) THEN
    EXECUTE 'ALTER TABLE "Movies" ALTER COLUMN "MovieFileId" DROP NOT NULL';
  END IF;
END $$;

-- 3) Null any refs where the file still doesn't exist
UPDATE "Movies" m
SET "MovieFileId" = NULL
WHERE m."MovieFileId" IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM "MovieFiles" mf WHERE mf."Id" = m."MovieFileId");

COMMIT;

-- Quick integrity checks after fix
SELECT COUNT(*) AS missing_metadata
FROM "Movies" m
LEFT JOIN "MovieMetadata" mm ON mm."Id" = m."MovieMetadataId"
WHERE mm."Id" IS NULL;

SELECT COUNT(*) AS missing_moviefile
FROM "Movies" m
LEFT JOIN "MovieFiles" mf ON mf."Id" = m."MovieFileId"
WHERE m."MovieFileId" IS NOT NULL AND mf."Id" IS NULL;

-- Check for unexpected 1:N between metadata and movies
SELECT "MovieMetadataId", COUNT(*) AS movie_count
FROM "Movies"
GROUP BY "MovieMetadataId"
HAVING COUNT(*) > 1
ORDER BY movie_count DESC;
SQL
  echo "✅ Integrity fix completed"
}

reset_sequences_local() {
  echo "🔧 Resetting all sequences (post-migration requirement)..."
  PGPASSWORD=$PG_PASSWORD psql -h "$PG_HOST" -p "${PG_PORT:-5432}" -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 <<'SQL'
-- Reset all sequences to prevent KeyNotFoundException and duplicate key errors
SELECT setval('public."MovieFiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "MovieFiles"));
SELECT setval('public."AlternativeTitles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "AlternativeTitles"));
SELECT setval('public."Blacklist_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Blocklist"));
SELECT setval('public."Collections_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Collections"));
SELECT setval('public."Commands_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Commands"));
SELECT setval('public."Config_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Config"));
SELECT setval('public."Credits_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Credits"));
SELECT setval('public."CustomFilters_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "CustomFilters"));
SELECT setval('public."CustomFormats_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "CustomFormats"));
SELECT setval('public."DelayProfiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "DelayProfiles"));
SELECT setval('public."DownloadClientStatus_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "DownloadClientStatus"));
SELECT setval('public."DownloadClients_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "DownloadClients"));
SELECT setval('public."DownloadHistory_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "DownloadHistory"));
SELECT setval('public."ExtraFiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "ExtraFiles"));
SELECT setval('public."History_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "History"));
SELECT setval('public."ImportExclusions_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "ImportExclusions"));
SELECT setval('public."ImportListMovies_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "ImportListMovies"));
SELECT setval('public."IndexerStatus_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "IndexerStatus"));
SELECT setval('public."Indexers_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Indexers"));
SELECT setval('public."MetadataFiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "MetadataFiles"));
SELECT setval('public."Metadata_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Metadata"));
SELECT setval('public."MovieMetadata_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "MovieMetadata"));
SELECT setval('public."MovieTranslations_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "MovieTranslations"));
SELECT setval('public."Movies_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Movies"));
SELECT setval('public."NamingConfig_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "NamingConfig"));
SELECT setval('public."Notifications_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Notifications"));
SELECT setval('public."PendingReleases_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "PendingReleases"));
SELECT setval('public."Profiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "QualityProfiles"));
SELECT setval('public."QualityDefinitions_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "QualityDefinitions"));
SELECT setval('public."Restrictions_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "ReleaseProfiles"));
SELECT setval('public."RootFolders_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "RootFolders"));
SELECT setval('public."ScheduledTasks_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "ScheduledTasks"));
SELECT setval('public."SubtitleFiles_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "SubtitleFiles"));
SELECT setval('public."Tags_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Tags"));
SELECT setval('public."Users_Id_seq"', (SELECT COALESCE(MAX("Id")+1,1) FROM "Users"));
SQL
  echo "✅ All sequences reset to prevent ID conflicts"
}

diagnostic_check() {
  echo "🔍 Running diagnostic queries to identify remaining issues..."
  PGPASSWORD=$PG_PASSWORD psql -h "$PG_HOST" -p "${PG_PORT:-5432}" -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 <<'SQL'
-- Any movie still pointing to a non-existent file?
SELECT m."Id", m."Title", m."MovieFileId"
FROM "Movies" m
LEFT JOIN "MovieFiles" mf ON mf."Id" = m."MovieFileId"
WHERE m."MovieFileId" IS NOT NULL AND mf."Id" IS NULL;

-- Any movie without metadata?
SELECT m."Id", m."Title", m."MovieMetadataId"
FROM "Movies" m
LEFT JOIN "MovieMetadata" mm ON mm."Id" = m."MovieMetadataId"
WHERE mm."Id" IS NULL;

-- Any metadata referenced by multiple movies (unexpected shape)?
SELECT "MovieMetadataId", COUNT(*) AS movie_count
FROM "Movies"
GROUP BY "MovieMetadataId"
HAVING COUNT(*) > 1
ORDER BY movie_count DESC;

-- Check sequence values vs max IDs
SELECT
  'Movies' as table_name,
  currval('public."Movies_Id_seq"') as sequence_val,
  COALESCE(MAX("Id"), 0) as max_id
FROM "Movies"
UNION ALL
SELECT
  'MovieFiles' as table_name,
  currval('public."MovieFiles_Id_seq"') as sequence_val,
  COALESCE(MAX("Id"), 0) as max_id
FROM "MovieFiles"
UNION ALL
SELECT
  'MovieMetadata' as table_name,
  currval('public."MovieMetadata_Id_seq"') as sequence_val,
  COALESCE(MAX("Id"), 0) as max_id
FROM "MovieMetadata";
SQL
  echo "✅ Diagnostic check completed"
}

load_pg_details_from_secret() {
  echo "🔑 Loading PostgreSQL connection details from secret..."
  local secret_name="radarr-secret"

  PG_HOST=${PG_HOST:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.RADARR__POSTGRES__HOST}' 2>/dev/null | base64 -d || true)}
  PG_PORT=${PG_PORT:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.RADARR__POSTGRES__PORT}' 2>/dev/null | base64 -d || true)}
  PG_DB=${PG_DB:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.RADARR__POSTGRES__MAINDB}' 2>/dev/null | base64 -d || true)}
  PG_USER=${PG_USER:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.RADARR__POSTGRES__USER}' 2>/dev/null | base64 -d || true)}
  PG_PASSWORD=${PG_PASSWORD:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.RADARR__POSTGRES__PASSWORD}' 2>/dev/null | base64 -d || true)}

  if [ -z "$PG_HOST" ]; then PG_HOST=$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.POSTGRES_IP}' 2>/dev/null | base64 -d || true); fi
  if [ -z "$PG_PORT" ]; then PG_PORT=$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.POSTGRES_PORT}' 2>/dev/null | base64 -d || echo 5432); fi
  if [ -z "$PG_DB" ]; then PG_DB=$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.POSTGRES_DB}' 2>/dev/null | base64 -d || true); fi
  if [ -z "$PG_USER" ]; then PG_USER=$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.POSTGRES_USER}' 2>/dev/null | base64 -d || true); fi
  if [ -z "$PG_PASSWORD" ]; then PG_PASSWORD=$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d || true); fi

  PG_SUPER_PASS=${PG_SUPER_PASS:-$(kubectl get secret -n "$NAMESPACE" "$secret_name" -o jsonpath='{.data.INIT_POSTGRES_SUPER_PASS}' 2>/dev/null | base64 -d || true)}

  missing=()
  [ -z "$PG_HOST" ] && missing+=("host")
  [ -z "$PG_PORT" ] && missing+=("port")
  [ -z "$PG_DB" ] && missing+=("database")
  [ -z "$PG_USER" ] && missing+=("user")
  [ -z "$PG_PASSWORD" ] && missing+=("password")
  [ -z "$PG_SUPER_PASS" ] && missing+=("superuser password (INIT_POSTGRES_SUPER_PASS)")
  if [ ${#missing[@]} -ne 0 ]; then
    echo "❌ Missing PostgreSQL secret values: ${missing[*]}"
    exit 1
  fi

  echo "📊 PostgreSQL (target)"
  echo "  Host: $PG_HOST"
  echo "  Port: $PG_PORT"
  echo "  Database: $PG_DB"
  echo "  User: $PG_USER"
}

scale_radarr() {
  local replicas="$1"
  echo "🛠️ Scaling Radarr to $replicas"
  kubectl scale deployment -n "$NAMESPACE" "$DEPLOYMENT" --replicas="$replicas"
  if [ "$replicas" = "0" ]; then
    kubectl rollout status deployment -n "$NAMESPACE" "$DEPLOYMENT" --timeout=180s || true
  else
    kubectl rollout status deployment -n "$NAMESPACE" "$DEPLOYMENT" --timeout=300s
    kubectl wait --for=condition=available deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=300s || true
  fi
}

get_radarr_pod() {
  kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name="$DEPLOYMENT" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

backup_sqlite_from_pod() {
  local pod_name
  pod_name=$(get_radarr_pod)
  if [ -z "$pod_name" ]; then
    echo "ℹ️ No Radarr pod found (likely scaled down already); skipping in-pod copy"
    return 0
  fi
  local ts
  ts=$(date +%Y%m%d_%H%M%S)
  local local_dir="${BACKUP_DIR:-/tmp/radarr-migration}"
  mkdir -p "$local_dir"
  echo "💾 Copying /config/radarr.db from pod $pod_name to $local_dir/radarr.db.$ts"
  kubectl exec -n "$NAMESPACE" "$pod_name" -- sh -c 'test -f /config/radarr.db && ls -l /config/radarr.db' || {
    echo "❌ SQLite DB not found at /config/radarr.db"; return 1;
  }
  kubectl cp -n "$NAMESPACE" "$pod_name:/config/radarr.db" "$local_dir/radarr.db.$ts"
  ls -lh "$local_dir/radarr.db.$ts" || true
  SQLITE_BACKUP_PATH="$local_dir/radarr.db.$ts"
}

psql_super_do() {
  local sql_payload="$1"
  local pod_name="pg-maint-$(date +%s)"
  echo "🧰 Running admin SQL via transient pod: $pod_name"
  kubectl run -n "$NAMESPACE" "$pod_name" --restart=Never --image=postgres:17 --env="PGHOST=$PG_HOST" --env="PGPORT=$PG_PORT" --env="PGPASSWORD=$PG_SUPER_PASS" --command -- bash -ceu "psql -h \"$PGHOST\" -U postgres -p \"$PGPORT\" -d postgres -v ON_ERROR_STOP=1 <<'SQL'\n${sql_payload}\nSQL\n" || {
    kubectl logs -n "$NAMESPACE" pod/"$pod_name" || true
    kubectl delete pod -n "$NAMESPACE" "$pod_name" --ignore-not-found=true >/dev/null 2>&1 || true
    return 1
  }
  kubectl delete pod -n "$NAMESPACE" "$pod_name" --wait=true --ignore-not-found=true >/dev/null 2>&1 || true
}

drop_and_recreate_db_and_role() {
  echo "🗂️ Dropping and recreating database and role for a pristine migration"
  psql_super_do "
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PG_DB}';
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_database WHERE datname = '${PG_DB}') THEN
    EXECUTE 'DROP DATABASE ' || quote_ident('${PG_DB}');
  END IF;
END
$do$;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${PG_USER}') THEN
    EXECUTE 'DROP ROLE ' || quote_ident('${PG_USER}');
  END IF;
END
$do$;
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${PG_USER}') THEN
    EXECUTE 'CREATE ROLE ' || quote_ident('${PG_USER}') || ' LOGIN PASSWORD ' || quote_literal('${PG_PASSWORD}');
  END IF;
END
$do$;
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${PG_DB}') THEN
    EXECUTE 'CREATE DATABASE ' || quote_ident('${PG_DB}') || ' OWNER ' || quote_ident('${PG_USER}');
  END IF;
END
$do$;
GRANT ALL PRIVILEGES ON DATABASE "${PG_DB}" TO "${PG_USER}";
" || return 1
}

run_pgloader_transient() {
  local src_db_path="${SQLITE_BACKUP_PATH:-}"
  if [ -z "$src_db_path" ] || [ ! -f "$src_db_path" ]; then
    echo "❌ SQLite backup not found; set SQLITE_BACKUP_PATH to a local radarr.db backup"
    return 1
  fi
  local pod_name="radarr-pgloader-$(date +%s)"
  echo "🚚 Launching pgloader pod: $pod_name"
  kubectl run -n "$NAMESPACE" "$pod_name" --restart=Never --image=ghcr.io/roxedus/pgloader --command -- sleep 3600
  echo "⏳ Waiting for pgloader pod to be ready..."
  kubectl wait --for=condition=ready pod/"$pod_name" -n "$NAMESPACE" --timeout=120s
  echo "📥 Copying SQLite backup into pod"
  kubectl cp -n "$NAMESPACE" "$src_db_path" "$pod_name:/tmp/radarr.db.backup"
  echo "🔄 Running pgloader import"
  kubectl exec -n "$NAMESPACE" "$pod_name" -- pgloader --type sqlite \
    --with "quote identifiers" \
    --with "data only" \
    --with "prefetch rows = 100" \
    --with "batch size = 1MB" \
    --with "concurrency = 1" \
    /tmp/radarr.db.backup \
    "postgresql://$PG_USER:$PG_PASSWORD@$PG_HOST:${PG_PORT:-5432}/$PG_DB"
  local rc=$?
  kubectl delete pod -n "$NAMESPACE" "$pod_name" --wait=true --ignore-not-found=true >/dev/null 2>&1 || true
  return $rc
}

psql_app_exec() {
  local sql_payload="$1"
  local pod_name="pg-client-$(date +%s)"
  kubectl run -n "$NAMESPACE" "$pod_name" --restart=Never --image=postgres:17 \
    --env="PGHOST=$PG_HOST" --env="PGPORT=$PG_PORT" --env="PGPASSWORD=$PG_PASSWORD" \
    --command -- bash -ceu "psql -h \"$PGHOST\" -U \"$PG_USER\" -p \"$PGPORT\" -d \"$PG_DB\" -v ON_ERROR_STOP=1 <<'SQL'\n${sql_payload}\nSQL\n" || {
      kubectl logs -n "$NAMESPACE" pod/"$pod_name" || true
      kubectl delete pod -n "$NAMESPACE" "$pod_name" --ignore-not-found=true >/dev/null 2>&1 || true
      return 1
    }
  kubectl delete pod -n "$NAMESPACE" "$pod_name" --wait=true --ignore-not-found=true >/dev/null 2>&1 || true
}

vacuum_analyze_db() {
  echo "🧹 Running VACUUM ANALYZE"
  psql_app_exec "VACUUM ANALYZE;" || true
}

orchestrate_all() {
  echo "🚦 Starting end-to-end migration orchestration"
  load_pg_details_from_secret

  echo "1) Quiesce Radarr"
  scale_radarr 0

  echo "2) Backup SQLite from pod (if present)"
  backup_sqlite_from_pod || return 1

  echo "3) Drop & recreate DB and role"
  drop_and_recreate_db_and_role || return 1

  echo "4) Run pgloader import"
  run_pgloader_transient || return 1

  echo "5) Apply integrity fixes"
  integrity_fix_local || return 1

  echo "6) Reset sequences"
  reset_sequences_local || return 1

  echo "7) Vacuum analyze"
  vacuum_analyze_db || true

  echo "8) Start Radarr"
  scale_radarr 1

  echo "9) Quick verification"
  kubectl logs -n "$NAMESPACE" -l app.kubernetes.io/name="$DEPLOYMENT" --tail=80 || true
  echo "🎉 End-to-end migration completed"
}

usage() {
  cat <<USAGE
Usage (in-pod):
  $0 inpod-backup         # copy /config/radarr.db to /tmp/radarr.db.backup
  $0 inpod-preclean       # delete conflicting tables before import
  $0 inpod-pgloader       # run pgloader from /tmp/radarr.db.backup into PG
  $0 inpod-fix            # integrity fixes (align Movies/MovieFiles, nullify orphans)
  $0 inpod-resetseq       # reset ALL sequences (comprehensive post-migration fix)
  $0 inpod-diagnostic     # run diagnostic queries to identify remaining issues
  $0 inpod-all            # backup -> preclean -> pgloader -> fix -> resetseq
  $0 inpod-postfix        # fix -> resetseq -> diagnostic (run after migration)

Orchestration:
  $0 run-all              # scale down -> backup -> dropdb -> pgloader -> fixes -> vacuum -> scale up
  $0 run-dropdb           # drop & recreate database and role (uses secrets)
  $0 run-pgloader         # run pgloader from local SQLITE_BACKUP_PATH into Postgres

Environment variables required:
  PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB
USAGE
}

if [ -n "$1" ]; then
  cmd="$1"
  case "$cmd" in
    inpod-backup)     backup_sqlite_local; exit $? ;;
    inpod-preclean)   preclean_tables; exit $? ;;
    inpod-pgloader)   run_pgloader_local; exit $? ;;
    inpod-fix)        integrity_fix_local; exit $? ;;
    inpod-resetseq)   reset_sequences_local; exit $? ;;
    inpod-diagnostic) diagnostic_check; exit $? ;;
    inpod-all)
      backup_sqlite_local || exit 1
      preclean_tables || true
      run_pgloader_local || exit 1
      integrity_fix_local || exit 1
      reset_sequences_local || exit 1
      exit 0
      ;;
    inpod-postfix)
      integrity_fix_local || exit 1
      reset_sequences_local || exit 1
      diagnostic_check || exit 1
      echo "🎉 Post-migration fixes completed. Restart Radarr now."
      exit 0
      ;;
    run-all)
      orchestrate_all; exit $? ;;
    run-dropdb)
      load_pg_details_from_secret; drop_and_recreate_db_and_role; exit $? ;;
    run-pgloader)
      load_pg_details_from_secret; run_pgloader_transient; exit $? ;;
    *) usage; exit 1 ;;
  esac
fi

# Kubernetes-based migration (commented out - use inpod-* functions instead)
# The functions above can be run directly in a postgres:17 or pgloader pod
# Example usage:
#   kubectl run -n media postgres-fix --rm -i --image postgres:17 --command -- /bin/bash
#   # Inside the pod:
#   #   kubectl cp migrate-to-postgres.sh media/postgres-fix:/tmp/
#   #   cd /tmp && chmod +x migrate-to-postgres.sh
#   #   ./migrate-to-postgres.sh inpod-postfix

echo ""
echo "🚨 NOTICE: The script now uses modular inpod-* functions."
echo ""
echo "For your KeyNotFoundException error, run this in a postgres:17 pod:"
echo ""
echo "  kubectl run -n media postgres-fix --rm -i --image postgres:17 --command -- /bin/bash"
echo "  # Inside the pod:"
echo "  #   kubectl cp /path/to/migrate-to-postgres.sh media/postgres-fix:/tmp/"
echo "  #   cd /tmp && chmod +x migrate-to-postgres.sh"
echo "  #   ./migrate-to-postgres.sh inpod-postfix"
echo ""
echo "Or run the sequence fix commands directly via psql (see usage above)."
echo ""
echo "🎯 To fix your current KeyNotFoundException:"
echo "   ./migrate-to-postgres.sh inpod-postfix"
echo ""
