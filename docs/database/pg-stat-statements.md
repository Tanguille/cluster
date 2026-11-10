# `pg_stat_statements`

## Overhead of `pg_stat_statements`

It is **not free**, but for typical clusters it is **small** compared to query execution:

- **CPU:** A little work on each statement to normalize text and hash into the shared hash table.
- **Memory:** Bounded by **`pg_stat_statements.max`** (each entry holds a normalized query text and counters). If you are memory-constrained, lower `max` before disabling the extension.
- **`track`:** **`top`** (the Postgres default; `cluster.yaml` sets only `max`) records only **top-level** statements; **`all`** also counts nested statements (e.g. inside functions) and costs more. Postgres docs recommend **`top`** for many production workloads.

If you need **zero** statement tracking, remove `pg_stat_statements` from `shared_preload_libraries` and drop the extension—then you lose top-query tooling.

## `pg_stat_statements` (top queries for tuning)

The cluster enables the extension via **`shared_preload_libraries`** and GUCs in `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml`. After Flux applies and instances roll, **create the extension in each database** where you want statement history (at least **`postgres`** for global tooling; repeat for app DBs as needed):

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

Run as a superuser or a role with rights to create extensions (e.g. `kubectl -n database exec -it <primary> -c postgres -- psql -U postgres -d yourdb`, with `<primary>` from the Cluster's `.status.currentPrimary`).

Then top-query tooling can use `pg_stat_statements` data.

## Rollout

Changing **`shared_preload_libraries`** requires a **rolling restart** of Postgres pods; schedule a short maintenance window if needed. Apply **only** this config change in one commit if you also change extension images—see [CloudNative-PG](https://cloudnative-pg.io/documentation/) guidance on avoiding simultaneous risky changes.
