# Postgres MCP: buffer stats, `pg_stat_statements`, and roles

## Buffer cache stats (empty in MCP health checks)

[crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) health tooling (PgHero-style) reads **per-table / per-index buffer statistics** from views such as `pg_statio_user_tables` and `pg_statio_user_indexes`. Those require:

1. **Privileges** — a minimal read-only login often cannot see stats views. Grant the predefined role **`pg_monitor`** (PostgreSQL 10+) to the MCP user so it can read `pg_statio_*`, `pg_stat_*`, and related monitoring views:

   ```sql
   GRANT pg_monitor TO your_mcp_username;
   ```

   Alternatively, **`pg_read_all_stats`** covers statistics views; **`pg_monitor`** is broader and is the usual choice for observability users.

2. **Where you connect** — buffer counts are **per instance**. A session on a **replica** (e.g. via `pgbouncer-ro`) reflects **that node’s** buffer cache, not the primary’s. For apples-to-apples “cache hit” work comparable to a single-host mental model, use **`pgbouncer-rw`** / primary when you explicitly want primary stats, or interpret replica stats as replica-local.

3. **Per-database** — `pg_statio_user_*` only include objects in the **current database** (`dbname` in the URI). Connect to the app database you care about when measuring table/index cache behavior.

## Overhead of `pg_stat_statements`

It is **not free**, but for typical clusters it is **small** compared to query execution:

- **CPU:** A little work on each statement to normalize text and hash into the shared hash table.
- **Memory:** Bounded by **`pg_stat_statements.max`** (each entry holds a normalized query text and counters). If you are memory-constrained, lower `max` before disabling the extension.
- **`track`:** **`top`** (what we use in `cluster.yaml`) records only **top-level** statements; **`all`** also counts nested statements (e.g. inside functions) and costs more. Postgres docs recommend **`top`** for many production workloads.

If you need **zero** statement tracking, remove `pg_stat_statements` from `shared_preload_libraries` and drop the extension—then you lose `get_top_queries`-style tooling.

## CloudNativePG 1.29.0 (Mar 31, 2026) and this setup

The [1.29.0 release notes](https://cloudnative-pg.io/docs/1.29/release_notes/v1.29/) do **not** introduce a feature that reduces `pg_stat_statements` overhead itself. Items that are **adjacent** to tuning / security:

| Area           | 1.29.0 note                                                                                                                                                                                                                                            |
|----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Extensions** | **PostgreSQL extensions in image catalogs** — `ImageCatalog` can carry extension-specific images; **`bin_path`** / **`env`** on `postgresql.extensions` help with extensions that ship extra binaries or env (e.g. future **hypopg**-style workflows). |
| **Network**    | **`podSelectorRefs`** for **`pg_hba.conf`** — resolve pod IPs by label so only expected workloads (e.g. ToolHive pods in `ai`) can reach Postgres, instead of broad CIDRs.                                                                             |
| **Pooler**     | **TLS cipher / protocol bounds** on **Pooler** — stricter compliance for client↔PgBouncer↔Postgres paths.                                                                                                                                              |
| **Replicas**   | **Role reconciliation** runs on the primary only when appropriate — fewer spurious errors on replicas when managing roles.                                                                                                                             |

Upgrading the **operator** to a chart that ships 1.29.x is separate from the Postgres **image** in `Cluster` spec; bump the chart version in Flux when you are ready to adopt 1.29.0 (see `kubernetes/apps/database/cloudnative-pg/app/ocirepository.yaml`).

## `pg_stat_statements` (top queries for tuning)

The cluster enables the extension via **`shared_preload_libraries`** and GUCs in `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml`. After Flux applies and instances roll, **create the extension in each database** where you want statement history (at least **`postgres`** for global tooling; repeat for app DBs as needed):

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

Run as a superuser or a role with rights to create extensions (e.g. on CloudNative-PG, `kubectl cnpg psql postgres16 -n database` then `\c yourdb` as appropriate).

Then postgres-mcp tools such as **`get_top_queries`** can use `pg_stat_statements` data.

## Rollout

Changing **`shared_preload_libraries`** requires a **rolling restart** of Postgres pods; schedule a short maintenance window if needed. Apply **only** this config change in one commit if you also change extension images—see [CloudNative-PG](https://cloudnative-pg.io/documentation/) guidance on avoiding simultaneous risky changes.

For **CNPG 1.29+ image catalogs, image-volume extensions, and declarative `Database` extensions** (vs pinned `imageName` + inline `extensions`), see **`cnpg-image-catalogs-and-extensions.md`** in this folder.
