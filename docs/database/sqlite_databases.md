# SQLite Databases Found in Cluster

This file lists all SQLite databases found across the cluster, organized by namespace and application.

## Namespace: media

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
- **PostgreSQL Support**: ❌ No (SQLite only)
- **Notes**: User management database for Jellyfin

---

## Summary

### PostgreSQL Support Status

#### ❌ Does NOT Support PostgreSQL (Cannot Migrate)

1. **jellyfin** - ❌ No PostgreSQL support (waiting for feature)
   - Playback reporting and introskipper plugins use SQLite only
2. **qbittorrent** - ❌ SQLite only (no PostgreSQL support)
3. **wizarr** - ❌ SQLite only (no PostgreSQL support)

### Notes

- Some apps (jellyfin) have backup files that are not active databases
- Apps without PostgreSQL support will continue using SQLite
