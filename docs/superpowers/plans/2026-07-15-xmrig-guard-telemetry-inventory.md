# XMRig Guard Telemetry Inventory

**Inventory time:** 2026-07-15T20:13:39Z
**Datasource:** Grafana UID `victoriametrics`
**Scope:** Task 1 live, read-only telemetry evidence. No cluster state was changed.

## Result and enforcement gate

Live VictoriaMetrics evidence establishes the NVMe sensor identities and the
`control-1` CPU metric contract below. It does **not** authorize label or
eviction enforcement: the separate Node authorization/NodeRestriction evidence
in this document remains unresolved. Keep enforcement observe-only.

## Discovery evidence

Metric-name discovery over the prior two hours returned all requested metrics:

```text
container_cpu_usage_seconds_total
node_cpu_seconds_total
node_hwmon_temp_celsius
```

Relevant live label names and values:

| Metric | Relevant labels | Observed values |
| --- | --- | --- |
| `node_hwmon_temp_celsius` | `kubernetes_node`, `nodename`, `chip`, `sensor` | nodes `control-1`, `control-2`, `control-3`; sensors `temp0`–`temp4`; NVMe chips `nvme_nvme0`, `nvme_nvme1` |
| `node_cpu_seconds_total` | `kubernetes_node`, `nodename`, `cpu`, `mode` | nodes `control-1`, `control-2`, `control-3`; modes include `idle`, `iowait`, `irq`, `nice`, `softirq`, `steal`, `system`, `user` |
| `container_cpu_usage_seconds_total` | `node`, `namespace`, `pod`, `container` | nodes `control-1`, `control-2`, `control-3`; namespace `web3`; regular container values exclude the empty and `POD` sandbox values |

## NVMe temperature contract

### Stable allowlists

The following identities were present continuously in 60-second range-query
samples for the last 15 minutes. The `chip`/`sensor` pair, together with the
immutable node name, is the controller allowlist identity; `instance` and
node-exporter pod names are scrape-target details and must not be configured as
identities.

```yaml
control-2:
  - {chip: nvme_nvme0, sensor: temp1}
  - {chip: nvme_nvme0, sensor: temp2}
  - {chip: nvme_nvme0, sensor: temp3}
  - {chip: nvme_nvme1, sensor: temp1}
  - {chip: nvme_nvme1, sensor: temp2}
  - {chip: nvme_nvme1, sensor: temp3}
  - {chip: nvme_nvme1, sensor: temp4}
control-3:
  - {chip: nvme_nvme0, sensor: temp1}
  - {chip: nvme_nvme0, sensor: temp2}
  - {chip: nvme_nvme0, sensor: temp3}
  - {chip: nvme_nvme0, sensor: temp4}
  - {chip: nvme_nvme1, sensor: temp1}
  - {chip: nvme_nvme1, sensor: temp2}
  - {chip: nvme_nvme1, sensor: temp3}
  - {chip: nvme_nvme1, sensor: temp4}
```

`control-1` remains NVMe-exempt and has no NVMe allowlist.

The controller must query exactly the configured identities, require the
complete expected set, and take its maximum; an extra, missing, malformed, or
stale expected series is invalid/unsafe. The inventory does not infer a device
path beyond node-exporter's stable `nvme_nvme0`/`nvme_nvme1` chip identity.

### Instant-query evidence

At source time `2026-07-15T20:13:01Z` (`1784146381.093`), the live values were:

| Node | `nvme_nvme0` °C | `nvme_nvme1` °C | Maximum °C |
| --- | --- | --- | ---: |
| `control-2` | `temp1=42.85`, `temp2=42.85`, `temp3=49.85` | `temp1=51.85`, `temp2=59.85`, `temp3=55.85`, `temp4=51.85` | 59.85 |
| `control-3` | `temp1=48.85`, `temp2=57.85`, `temp3=53.85`, `temp4=48.85` | `temp1=55.85`, `temp2=64.85`, `temp3=59.85`, `temp4=55.85` | 64.85 |

The full label shape on those series was:

```text
__name__, chip, container=node-exporter, endpoint=metrics, instance,
job=node-exporter, kubernetes_node, namespace=observability, nodename, pod,
prometheus=observability/victoria-metrics, sensor, service=node-exporter
```

The 15-minute range queries returned each allowlisted series at 60-second
steps. `control-2` returned 13 points from `1784145687.228` through
`1784146407.228`; `control-3` returned 16 points from `1784145507.325` through
`1784146407.228`. This is live evidence of a roughly 60-second source cadence,
not a promise of future freshness.

### Configured timing values

Use the observed cadence conservatively:

```yaml
evaluationInterval: 60s
sourceSampleMaxAge: 120s
cpuRateWindow: 5m
maxReactionLatency: 180s # one evaluation interval plus max source age
```

The controller must retain and compare each telemetry source timestamp. A
repeated query response with the same source timestamp is not a new observation
and cannot advance a dwell timer.

## `control-1` non-XMRig CPU contract

### Source labels and observed values

At `2026-07-15T20:13:02Z` (`1784146382.061`),
`node_cpu_seconds_total{kubernetes_node="control-1",mode!="idle"}` returned
the node-exporter label shape shown above for CPUs `0`–`11` and all non-idle
modes. Example source values were `cpu="0",mode="user" = 195692.4` and
`cpu="0",mode="system" = 23072.91` seconds.

At `2026-07-15T20:13:01Z` (`1784146381.096`), the requested cAdvisor selector
on `control-1`/`web3` returned a regular `p2pool` container, proving the
cAdvisor source and labels:

```text
container=app, cpu=total, instance=control-1, job=kubelet,
kubernetes_io_hostname=control-1, metrics_path=/metrics/cadvisor,
namespace=web3, node=control-1, pod=p2pool-865cf6d757-7xdf9
```

Its value was `1486.478276` seconds. A node-wide non-sandbox cAdvisor count at
`2026-07-15T20:13:27Z` was `138`. No running `pod=~"xmrig-.*"` series was
returned; that is the expected zero-XMRig case, not missing cAdvisor telemetry.

### Exact PromQL

The controller's `control-1` non-XMRig CPU-percent result is the following
single scalar instant query (fixed 5-minute rate window):

```promql
clamp_max(clamp_min(100 * ((sum(rate(node_cpu_seconds_total{kubernetes_node="control-1",mode!="idle"}[5m])) / count(count(node_cpu_seconds_total{kubernetes_node="control-1",mode="idle"}) by (cpu))) - ((sum(rate(container_cpu_usage_seconds_total{node="control-1",namespace="web3",pod=~"xmrig-.*",container!="",container!="POD"}[5m])) or vector(0)) / count(count(node_cpu_seconds_total{kubernetes_node="control-1",mode="idle"}) by (cpu)))), 0), 100)
```

It derives host busy percent from all non-idle node CPU modes, divides by the
12 observed host CPUs, subtracts XMRig's non-sandbox cAdvisor CPU share, uses
zero only when the XMRig selector is absent, and clamps the result to 0–100.
The controller must separately verify that host and non-sandbox cAdvisor
sources exist and are fresh; it must not treat an absent generic cAdvisor
source as the zero-XMRig case.

The expression evaluated successfully at `2026-07-15T20:13:27Z`
(`1784146407.258`) and returned `37.05277777777771` percent. Its XMRig operand
was absent at that instant, so the `or vector(0)` branch was exercised.

## NodeRestriction gate remains unresolved

Repository evidence remains insufficient to claim Node authorization and the
NodeRestriction admission plugin are enabled. `talos/patches/controller/cluster.yaml`
deletes explicit `admissionControl` configuration, and no generated apiserver
configuration was available. Therefore neither
`node-restriction.kubernetes.io/xmrig-nvme-safe` nor
`node-restriction.kubernetes.io/xmrig-cpu-available` may be enforced until
read-only generated/live apiserver evidence positively confirms both controls.

## Tooling evidence

Read-only Grafana MCP discovery and query operations were run against
`victoriametrics`: metric-name discovery; label-name/value discovery; instant
queries for all three requested metrics; 15-minute NVMe range queries; and the
composed CPU contract query. No live-cluster mutation, reconciliation, or
enforcement action was performed.
