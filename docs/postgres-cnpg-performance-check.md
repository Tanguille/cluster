# Check PostgreSQL (CloudNative-PG) performance after tuning

Use this after tuning (e.g. NVMe/PLP, work_mem, autovacuum) to see if performance improved. Run from a machine with `kubectl` and cluster access, or use the **observability** MCP (Grafana/Prometheus) when connected.

## 1. Prometheus (PromQL)

Port-forward then query, or use Grafana Explore with datasource `prometheus`:

```bash
kubectl port-forward -n observability svc/prometheus-operated 9090:9090
```

Then open `http://localhost:9090` and run these in the Graph/Query UI.

**Cache hit ratio** (higher is better; aim > 99%):

```promql
sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m]))
/
(sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m])) + sum by (pod) (rate(cnpg_pg_stat_database_blks_read[5m])))
```

**Transactions per second** (primary, all DBs):

```promql
sum(rate(cnpg_pg_stat_database_xact_commit{cnpg_pg_replication_in_recovery="0"}[5m]))
```

**Checkpoints** (timed vs requested; fewer requested = good WAL/checkpoint tuning):

```promql
sum by (pod) (increase(cnpg_pg_stat_checkpointer_checkpoints_timed[24h]))
sum by (pod) (increase(cnpg_pg_stat_checkpointer_checkpoints_req[24h]))
```

**Temp files** (lower = less work_mem spilling):

```promql
sum by (pod, datname) (increase(cnpg_pg_stat_database_temp_files[24h]))
```

**Max transaction duration** (slow queries):

```promql
cnpg_backends_max_tx_duration_seconds
```

## 2. Direct SQL (primary pod)

From the primary Postgres pod you get exact buffer cache hit ratio and row stats:

```bash
# Find primary (role=primary)
kubectl get pods -n database -l cnpg.io/cluster=postgres16 -l cnpg.io/instanceRole=primary -o name

# Exec into primary (replace postgres16-2 if different; get name from the get pods command above)
kubectl exec -n database postgres16-2 -c postgres -- psql -U postgres -t -A -c "
SELECT
  round(100.0 * sum(blks_hit) / nullif(sum(blks_hit) + sum(blks_read), 0), 2) AS cache_hit_pct,
  sum(xact_commit) AS commits,
  sum(tup_returned) AS tup_returned,
  sum(tup_fetched) AS tup_fetched,
  sum(tup_inserted) AS tup_inserted,
  sum(tup_updated) AS tup_updated,
  sum(temp_files) AS temp_files,
  sum(temp_bytes) AS temp_bytes
FROM pg_stat_database
WHERE datname NOT IN ('template0','template1');
"
```

Interpretation:

- **cache_hit_pct** — Should be high (> 99%) with 2GB shared_buffers and NVMe.
- **temp_files / temp_bytes** — Should stay low with work_mem 12MB; if high, consider per-session `SET work_mem` for heavy queries.
- **commits / tup_*** — Throughput; compare before/after if you have a baseline.

## 3. Observability MCP

When the **observability** MCP server is connected, use `grafana_query_prometheus` to run the same PromQL as in section 1.

**Setup:** Resolve the Prometheus datasource UID (e.g. `grafana_list_datasources` with `type: "prometheus"` or `grafana_get_datasource_by_name` with `name: "prometheus"`). Typical UID: `prometheus`.

**Tool:** `grafana_query_prometheus` with:

- `datasourceUid`: `"prometheus"` (or the UID from the step above)
- `expr`: one of the PromQL expressions below
- `startTime`: `"now"` for current snapshot
- `queryType`: `"instant"` for a single point, or `"range"` for a trend
- For range: also set `endTime`: `"now"`, `startTime`: `"now-24h"`, `stepSeconds`: `900` (15 min)

**Queries to run (same as section 1):**

| What | PromQL |
|------|--------|
| Cache hit ratio (by pod) | `sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m])) / (sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m])) + sum by (pod) (rate(cnpg_pg_stat_database_blks_read[5m])))` |
| Transactions/s (cluster) | `sum(rate(cnpg_pg_stat_database_xact_commit[5m]))` |
| Checkpoints timed (24h) | `sum by (pod) (increase(cnpg_pg_stat_checkpointer_checkpoints_timed[24h]))` |
| Checkpoints requested (24h) | `sum by (pod) (increase(cnpg_pg_stat_checkpointer_checkpoints_req[24h]))` |
| Temp files (24h) | `sum by (pod, datname) (increase(cnpg_pg_stat_database_temp_files[24h]))` |
| Max tx duration | `cnpg_backends_max_tx_duration_seconds` |

Use **instant** for a snapshot; use **range** with `now-24h` → `now` and `stepSeconds: 900` to see cache hit (or other metrics) over the last 24h. You can also use Grafana dashboards (e.g. CloudNative-PG / Postgres) for the same metrics.

## 4. Memory cost vs benefit / longer-term comparison

Current tuning uses a lot of memory (see `cluster.yaml`): **shared_buffers 2GB**, **work_mem 12MB** (peak ~2.4GB with 200 conn), **maintenance_work_mem 1GB**, **hugepages 2Gi**, **limit 8Gi** per instance. If cache hit stays in the 80–90% range and doesn’t approach >99%, extra shared_buffers may not be paying off — the working set may be larger than 2GB or not very cache-friendly, so more RAM doesn’t improve hit rate.

**What the optimization clearly helps:**

- **Checkpoints:** 2 requested in 24h (WAL/checkpoint tuning is effective).
- **Temp files:** Low overall; only jfstat showed moderate temp usage (~185 files in 24h). work_mem 12MB is likely reducing spill elsewhere.
- **No long-running transactions** in observed snapshots.

**To compare over a longer period** (e.g. 7d), run these with the observability MCP or Grafana (range queries, `startTime`: `now-7d`, `endTime`: `now`, `stepSeconds`: `3600`):

| Goal | PromQL |
|------|--------|
| Cache hit trend (cluster) | `sum(rate(cnpg_pg_stat_database_blks_hit[5m])) / (sum(rate(cnpg_pg_stat_database_blks_hit[5m])) + sum(rate(cnpg_pg_stat_database_blks_read[5m])))` |
| Disk read rate (by pod) | `sum(rate(cnpg_pg_stat_database_blks_read[5m])) by (pod)` |
| Temp files over 7d | `sum(increase(cnpg_pg_stat_database_temp_files[7d])) by (pod, datname)` |
| Throughput trend | `sum(rate(cnpg_pg_stat_database_xact_commit[5m]))` |

If cache hit stays flat in the 80s over 7d, consider **reducing shared_buffers** (e.g. to 1GB) to free memory; keep **work_mem** and **WAL/checkpoint** settings — they show measurable benefit. If you have a baseline from before tuning, compare blks_read rate and temp_files; lower blks_read or temp_files after tuning would indicate the optimization did help despite the modest cache hit ratio.

**Example 7d snapshot (from observability MCP):**

- **Cache hit (cluster):** 52–95% over 7d (hourly); often in the 70–90% band; latest ~80%. No sustained >99%.
- **blks_read by pod:** The **primary** (whichever pod) consistently shows 1.5–6k blocks/s read; replicas near 0. So disk read is inherent to the primary; 2GB shared_buffers is not eliminating it.
- **Temp files (7d):** jfstat ~236–301 per instance; crowdsec 1–3; all other DBs 0. work_mem is containing spill except for jfstat.
- **Throughput (xact_commit):** 1–4k/s depending on load and role changes; latest ~2.3k/s.

**Takeaway:** Cache hit and primary blks_read do not show a clear win from the current shared_buffers size over 7d. Reducing shared_buffers (e.g. to 1GB) is reasonable to save memory; keep work_mem and WAL/checkpoint tuning.
