# Prometheus cluster health — PromQL reference

Replace `$WINDOW` (default `30m`) and `$STEP` (`1m` for windows ≤ ~2h, `5m` for longer) together.

**CPU rates:** inner `rate(...[5m])` in **cores**; outer `avg_over_time` / `max_over_time` over `$WINDOW`.

## Step 1 — Alerts

**Prefer Grafana MCP:** `alerting_manage_rules`

**Fallback instant query:**

```promql
ALERTS{alertstate="firing"}
```

Summarize by `alertname`, `severity`, `namespace`, `pod`, `job`. Empty result ≠ guaranteed healthy (wrong datasource or missing rules).

## Step 2 — CPU by workload

**Per pod — average cores over `$WINDOW`:**

```promql
avg_over_time(
  (
    sum by (namespace, pod) (
      rate(container_cpu_usage_seconds_total{
        container!="POD",
        container!="",
        image!=""
      }[5m])
    )
  )[$WINDOW:$STEP]
)
```

**Per pod — peak over `$WINDOW`:**

```promql
max_over_time(
  (
    sum by (namespace, pod) (
      rate(container_cpu_usage_seconds_total{
        container!="POD",
        container!="",
        image!=""
      }[5m])
    )
  )[$WINDOW:$STEP]
)
```

**Per namespace — average over `$WINDOW`:**

```promql
avg_over_time(
  (
    sum by (namespace) (
      rate(container_cpu_usage_seconds_total{
        container!="POD",
        container!="",
        image!=""
      }[5m])
    )
  )[$WINDOW:$STEP]
)
```

**Usage vs CPU request** (requires kube-state-metrics; denominator is instant request):

```promql
avg_over_time(
  (
    sum by (namespace, pod) (
      rate(container_cpu_usage_seconds_total{
        container!="POD",
        container!=""
      }[5m])
    )
  )[$WINDOW:$STEP]
)
/
sum by (namespace, pod) (
  kube_pod_container_resource_requests{resource="cpu"}
)
```

### Interpretation bands (per-pod cores — tune for your fleet)

| Band | Signal |
|------|--------|
| `< 0.05` | Idle / low background |
| `0.05–0.5` | Normal steady work |
| `0.5–2` | Busy (indexing, sync, batch) |
| `> 2` | Heavy or investigate if sustained |

Relate to expected workloads before calling a pod “stuck.”

## Step 3 — Memory (optional)

**Peak working set bytes per pod over `$WINDOW`:**

```promql
max_over_time(
  (
    sum by (namespace, pod) (
      container_memory_working_set_bytes{
        container!="POD",
        container!="",
        image!=""
      }
    )
  )[$WINDOW:$STEP]
)
```

Report GiB: divide by `1024^3`. State peak vs avg.

## Step 4 — Node CPU (optional)

```promql
avg_over_time(
  (
    100 - (
      avg by (instance) (
        rate(node_cpu_seconds_total{mode="idle"}[5m])
      ) * 100
    )
  )[$WINDOW:$STEP]
)
```

Label as approximate non-idle % averaged over the window.

## Report template

```text
CLUSTER HEALTH SNAPSHOT (Prometheus, last <window>)

Alerts: none | <alertname + key labels>

CPU (avg cores over <window>) — top pods:
- <ns>/<pod>: <cores>

Memory (peak working set over <window>) — top pods (GiB):
- ...

Summary: One sentence; flag mismatches only with metric evidence.
```

## Fallback without MCP

```bash
kubectl port-forward -n observability svc/prometheus-operated 9090:9090
```

Use Prometheus Graph UI or query API; Grafana Explore uses the same datasource as MCP `query_prometheus`.
