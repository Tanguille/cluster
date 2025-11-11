# SQLite Databases Found in Cluster

This file lists all SQLite databases found across the cluster, organized by namespace and application.

## Namespace: media

### bazarr

- **Path**: `/config/db/bazarr.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ✅ Yes
- **Notes**: Uses different env var format than Servarr apps (`POSTGRES_ENABLED`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DATABASE`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`)
- **Migration**: Requires manual database creation + pgloader for data migration

### cross-seed

- **Path**: `/config/cross-seed.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❓ Unknown - needs verification
- **Notes**: Cross-seeding tool for Radarr/Sonarr

### jellyfin

- **Path**: `/config/data/playback_reporting.db`
- **Status**: ⏳ Waiting for postgres support
- **PostgreSQL Support**: ❌ No (not yet supported)
- **Notes**: Playback reporting database - Jellyfin doesn't support PostgreSQL yet
- **Path**: `/config/data/introskipper/introskipper.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❌ No (plugin-specific)
- **Notes**: Intro skipper plugin database
- **Path**: `/config/data/SQLiteBackups/20251020082050_jellyfin.db`
- **Status**: ⏳ Not migrated
- **Notes**: Backup file (not active)

### music-assistant

- **Path**: `/data/library.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❓ Unknown - needs verification
- **Notes**: Main library database
- **Path**: `/data/.cache/cache.db`
- **Status**: ⏳ Not migrated
- **Notes**: Cache database

### qbittorrent

- **Path**: `/config/qBittorrent/torrents.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❌ No (SQLite only)
- **Notes**: Torrent metadata database - qBittorrent only supports SQLite

### fileflows

- **Path**: `/app/Data/Data/FileFlows.sqlite`
- **Status**: ⏳ Not planned because it will be scheduled on a single node anyways
- **PostgreSQL Support**: ✅ Yes (requires valid license)
- **Notes**: FileFlows supports PostgreSQL and MySQL, but requires a Personal or Commercial license. Personal Free plan is limited to SQLite only. Configuration is done through the web console (Settings > Database), not environment variables.

### wizarr

- **Path**: `/data/database/database.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❓ Unknown - needs verification
- **Notes**: User management database for Jellyfin

## Namespace: ai

### n8n

- **Path**: Not found in standard locations (likely in `/home/node/.n8n`)
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ✅ Yes (native support)
- **Notes**: Uses `DB_SQLITE_VACUUM_ON_STARTUP` and `DB_SQLITE_POOL_SIZE` env vars, not worth to migrate since I barely use it
- **Migration**: Requires PostgreSQL env vars (`DB_TYPE=postgresdb`, `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_DATABASE`, etc.)

## Namespace: default

### trilium

- **Path**: `/home/node/trilium-data/document.db`
- **Status**: ⏳ Not migrated
- **PostgreSQL Support**: ❌ No (SQLite only)
- **Notes**: Main document database - Trilium Notes only supports SQLite
- **Path**: `/home/node/trilium-data/backup/backup-monthly.db`
- **Status**: ⏳ Not migrated
- **Notes**: Monthly backup (not active)
- **Path**: `/home/node/trilium-data/backup/backup-weekly.db`
- **Status**: ⏳ Not migrated
- **Notes**: Weekly backup (not active)

---

## Summary

### PostgreSQL Support Status

#### ✅ Supports PostgreSQL (Can Migrate)

1. **bazarr** - ✅ Supports PostgreSQL (since v1.1.5-beta.8)
   - Uses env vars: `POSTGRES_ENABLED`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DATABASE`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`
   - Requires manual database creation + pgloader migration
2. **n8n** - ✅ Supports PostgreSQL (native support)
   - Uses env vars: `DB_TYPE=postgresdb`, `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_DATABASE`, etc.
   - Note: User marked as "not worth migrating" (low usage)

#### ❌ Does NOT Support PostgreSQL (Cannot Migrate)

1. **jellyfin** - ❌ No PostgreSQL support (waiting for feature)
   - Playback reporting and introskipper plugins use SQLite only
2. **qbittorrent** - ❌ SQLite only (no PostgreSQL support)
3. **trilium** - ❌ SQLite only (no PostgreSQL support)

#### ❓ Unknown PostgreSQL Support (Needs Verification)

1. **cross-seed** - ❓ Unknown - needs verification
2. **music-assistant** - ❓ Unknown - needs verification
3. **wizarr** - ❓ Unknown - needs verification

### Notes

- Some apps (jellyfin, trilium) have backup files that are not active databases
- Apps without PostgreSQL support will continue using SQLite
