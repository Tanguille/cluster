# Postgres MCP (crystaldba) in ToolHive — design

**Date:** 2026-04-01
**Status:** Approved for implementation
**Goal:** Expose [postgres-mcp](https://github.com/crystaldba/postgres-mcp) via ToolHive so agents can inspect PostgreSQL health, settings, and workload signals to **inform CloudNativePG `postgresql.parameters` tuning** in Git (Flux), not to replace GitOps.

## Scope

- **In scope:** Read-oriented MCP tools (`analyze_db_health`, `execute_sql` for `pg_settings` / catalog, optional slow-query / index tools when extensions exist), using a **restricted** access mode and a **read-only** connection path to the CNPG cluster.
- **Out of scope:** Multi-database interactive switching (PostgreSQL is one database per connection); public HTTPRoute exposure without additional auth review (see Security).

## Connection

| Field | Choice |
|--------|--------|
| Host | `pgbouncer-ro.database.svc.cluster.local:5432` (RO PgBouncer; same as `docs/database/nextcloud-postgres-read-replica.md`) |
| Database | `postgres` (catalog / instance-level inspection) |
| URI | Stored as `POSTGRES_MCP_DATABASE_URI` in `toolhive-secrets` (SOPS), mapped to env `DATABASE_URI` for the MCP image |
| Access | `--access-mode=restricted` (postgres-mcp read-only transaction + SQL guardrails) |
| DB role | Separate role with `CONNECT` on `postgres` and **read-only** grants as appropriate; do not use superuser for this MCP |

## ToolHive wiring

- **`MCPServer`** `postgres-mcp`: image `crystaldba/postgres-mcp`, `transport: stdio`, `groupRef: database`, `args: ["--access-mode=restricted"]`, `spec.secrets` for `DATABASE_URI`.
- **`MCPGroup`** `database`: description for Postgres tuning / health.
- **`VirtualMCPServer`** `database`: aggregates the `database` group (in-cluster `vmcp-database` on port 4483, path `/mcp` — same pattern as other groups).
- **HTTPRoute:** **`mcp-database.${SECRET_DOMAIN}`** → `vmcp-database:4483` (same pattern as flux/resources). Same anonymous-gateway model as other ToolHive MCPs; treat DB access as sensitive.

## Operational notes

1. **Secrets:** Add `POSTGRES_MCP_DATABASE_URI` to `kubernetes/apps/ai/toolhive/app/secret.sops.yaml` with `sops` (full `postgresql://...` URI including credentials). Until present, the `postgres-mcp` workload will not start cleanly.
2. **Extensions:** Full index tuning / top-query features may require `pg_stat_statements` and `hypopg` on the cluster; optional follow-up in CNPG cluster manifests.
3. **Tuning workflow:** MCP recommends → human edits `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` → Flux reconcile.

## References

- Upstream: [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp)
- Poolers: `kubernetes/apps/database/cloudnative-pg/cluster/pooler-ro.yaml`
