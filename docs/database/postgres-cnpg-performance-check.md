# Check PostgreSQL (CloudNative-PG) performance after tuning

Use this after tuning (e.g. NVMe/PLP, work_mem, autovacuum) to see if performance improved. Run from a machine with `kubectl` and cluster access, or use the Grafana MCP (via the ToolHive vMCP) when connected.

## 1. VictoriaMetrics VMSingle (PromQL)

Port-forward VMSingle directly, or use Grafana Explore with the canonical `prometheus` datasource now backed by VictoriaMetrics/VMSingle:

```bash
kubectl port-forward -n observability svc/vmsingle-victoria-metrics 8428:8428
```

Then open `http://localhost:8428` and run these in the Graph/Query UI.

**Cache hit ratio** (higher is better; aim > 99%):

```promql
sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m]))
/
(sum by (pod) (rate(cnpg_pg_stat_database_blks_hit[5m])) + sum by (pod) (rate(cnpg_pg_stat_database_blks_read[5m])))
```

**Transactions per second** (primary, all DBs):

```promql
sum(rate(cnpg_pg_stat_database_xact_commit[5m]) and on(pod) (cnpg_pg_replication_in_recovery == 0))
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
primary=$(kubectl -n database get cluster postgres16 -o jsonpath='{.status.currentPrimary}')
kubectl exec -n database "$primary" -c postgres -- psql -U postgres -t -A -c "
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

- **cache_hit_pct** — Should be high (> 99%) with a well-sized shared_buffers and NVMe.
- **temp_files / temp_bytes** — Should stay low with work_mem 16MB; if high, consider per-session `SET work_mem` for heavy queries.
- **commits / tup_*** — Throughput; compare before/after if you have a baseline.

## 3. Grafana MCP

With the Grafana MCP (via the ToolHive vMCP) connected, run the section 1 PromQL through `grafana_query_prometheus` on the `prometheus` datasource (`queryType: "instant"` for a snapshot; `"range"` with `startTime: "now-24h"`, `stepSeconds: 900` for a trend).

## 4. Memory cost vs benefit / longer-term comparison

Current tuning and the reason for each value live in `cluster.yaml`. If cache hit stays in the 80–90% range and doesn’t approach >99%, extra shared_buffers is not paying off — the working set is larger than shared_buffers or not very cache-friendly, so more RAM doesn’t improve hit rate.

**To compare over a longer period** (e.g. 7d), run these with the Grafana MCP (via the ToolHive vMCP) or Grafana (range queries, `startTime`: `now-7d`, `endTime`: `now`, `stepSeconds`: `3600`):

| Goal | PromQL |
|------|--------|
| Cache hit trend (cluster) | `sum(rate(cnpg_pg_stat_database_blks_hit[5m])) / (sum(rate(cnpg_pg_stat_database_blks_hit[5m])) + sum(rate(cnpg_pg_stat_database_blks_read[5m])))` |
| Disk read rate (by pod) | `sum(rate(cnpg_pg_stat_database_blks_read[5m])) by (pod)` |
| Temp files over 7d | `sum(increase(cnpg_pg_stat_database_temp_files[7d])) by (pod, datname)` |
| Throughput trend | `sum(rate(cnpg_pg_stat_database_xact_commit[5m]))` |

**work_mem** and **WAL/checkpoint** settings were kept, as they show measurable benefit. If you have a baseline from before tuning, compare blks_read rate and temp_files; lower blks_read or temp_files after tuning would indicate the optimization did help despite the modest cache hit ratio.
