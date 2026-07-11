---
name: prometheus-cluster-health
description: >-
  Summarize Kubernetes cluster health from Prometheus: firing alerts, CPU/memory hotspots,
  and workload signal. Default lookback is 30 minutes unless the user specifies another window.

  user: "cluster health" / "health snapshot" → alerts + top CPU pods over $WINDOW
  user: "firing alerts" → list_alert_rules or ALERTS PromQL fallback
  user: "top CPU pods" / "how's the load" → container_cpu usage queries (cores, not sec/min)
  user: "is karakeep healthy?" → filter namespace/pod + correlate alerts

  Prefer observability MCP (Grafana / ToolHive group): read session tool schemas before calling.
compatibility: Requires observability MCP or VictoriaMetrics API access (kubectl port-forward -n observability svc/vmsingle-victoria-metrics 8428). Confirm prefixed tool names (e.g. grafana_query_prometheus) in-session.
---

# Prometheus cluster health snapshot

Answer “is the cluster OK?” and “who is using CPU?” with **correct units** (CPU in **cores**) via Grafana MCP or Prometheus API.

## Default lookback

- **Default `$WINDOW`:** `30m`. User overrides: `15m`, `1h`, `2h`, etc. — use consistently in all queries.
- **`$STEP`:** `1m` for windows up to ~2h; `5m` for longer windows.
- **Instant snapshots:** subquery patterns `[$WINDOW:$STEP]` around inner `rate(...[5m])`.

## Tool usage (observability MCP)

Stack: Grafana MCP (`grafana/mcp-grafana`), often behind ToolHive **observability** group. Datasource uid `prometheus` (default) — VictoriaMetrics behind `vmauth-victoria-metrics.observability:8427`; second uid `victoriametrics`.

**Before any call:** resolve real tool names in this session (may be prefixed, e.g. `grafana_query_prometheus`).

| Goal | Tools |
|------|--------|
| Datasource UID | `list_datasources`, `get_datasource_by_uid` |
| Firing alerts | **`list_alert_rules`**, `list_alert_groups` |
| PromQL | **`query_prometheus`** (`queryType`, `startTime`, `endTime`, `stepSeconds`) |
| Discovery | `list_prometheus_metric_names`, `list_prometheus_label_*` |
| Dashboards | `search_dashboards`, `get_dashboard_summary`, `get_dashboard_panel_queries` |
| Logs follow-up | none via Grafana MCP (no Loki) — VictoriaLogs LogsQL via vmauth :8427 `/select/logsql/*` or victoria-logs.observability:9428 |

**`query_prometheus`:** use `instant` + subquery PromQL from [references/promql-queries.md](references/promql-queries.md); for `range`, align `stepSeconds` with `$STEP` (60 for 1m, 300 for 5m).

Recommended flow: datasource UID → alerting rules → CPU/memory PromQL → discovery if empty → optional dashboards/logs.

## Principles

1. `rate(container_cpu_usage_seconds_total[...])` is **cores** — `0.25` ≈ ¼ core; never label as sec/min.
2. Short inner `rate` window (`5m`); outer `$WINDOW` for “recent” behavior.
3. Cadvisor labels: `namespace`, `pod`, `container` — verify; legacy `pod_name` may exist on old scrapes.
4. Filter noise: `container!="POD"`, `container!=""`, often `image!=""`.
5. Watchdog-style always-firing alerts may be intentional — use runbooks.
6. Do not hardcode workload names; use workspace context when available.

## Workflow

1. Set `$WINDOW` / `$STEP` from user request.
2. Alerts — Grafana rules, then PromQL fallback ([references/promql-queries.md](references/promql-queries.md#step-1--alerts)).
3. CPU — avg or max cores per pod/namespace; top N sorted ([references/promql-queries.md](references/promql-queries.md#step-2--cpu-by-workload)).
4. Optional memory / node queries — same reference.
5. Report using template in [references/promql-queries.md](references/promql-queries.md#report-template).

## Progressive disclosure

- PromQL expressions and interpretation bands: [references/promql-queries.md](references/promql-queries.md)

Format reference: [agentskills.io](https://agentskills.io/specification).
