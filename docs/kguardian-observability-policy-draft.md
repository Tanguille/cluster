# Observability namespace policy draft

Phase 2 local proposal, 2026-10-28. Branch: `harden/kguardian-default-observability`;
worktree: `.claude/worktrees/kguardian-default-observability`; base: `fdd3cd954`.
No audit policy or enforcement has been deployed. The branch wires a real CNP
into the namespace Kustomization; do not merge or reconcile before the separate
audit and owner approval gates.

The [new namespace policy](../kubernetes/apps/observability/networkpolicy.yaml)
selects regular pods for default-deny ingress/egress, retaining host probes and
unported kube-dns reply ingress. App rules allow configured callers, API clients,
scrapers and external integrations. The speedtest chart's additive native
NetworkPolicy is also narrowed to vmagent, so it cannot preserve the previous
whole-namespace scrape grant.

## Evidence and intended paths

Checked fresh namespace traffic, workload/Service/route/policy inventory, chart
configuration and generated policies for all 44 pod names: 32 succeeded and 12
had no captured traffic. The latter include DRM, iGPU, power, SMART, one node
exporter and log collectors. Some successful responses also contain no egress.
Neither case is a safe verdict. Responses do not establish seven days of capture.
The namespace traffic endpoint returned zero records/pods/drops despite the
generated policies: this is missing or inconsistent coverage, not a clean
traffic or drop baseline. Resolve it before audit.
Raw peer addresses and configuration remain outside this public repository.

| Workload group | Proposed paths beyond host/DNS |
|---|---|
| vmagent | Scrape ingress to each exporter on its pod target port. Broad pod egress across namespaces remains an explicit collector exception for dynamic ServiceMonitor discovery. An explicit namespace-exists selector excludes host/node identities; node scrape ports, API and configured LAN targets use separate rules. No unrestricted world grant. |
| Gatus | Internal UI and isolated public probe 8080; API route discovery, pooler 5432, Envoy pod TLS 10443, AI unified MCP 4483, public HTTPS/TCP DNS and ICMP echo. Labels are `gatus-sidecar`/instance `gatus`, including chart 0.6.0's current workload. |
| Kromgo | Public gateway and Homepage on pod 8080; vmagent on metrics pod 8081; vmsingle 8428. Service ports are 80 and 8080 respectively. |
| Grafana | Internal gateway, Homepage, vmagent, operator and AI ToolHive backend 3000; vmauth 8427, vmsingle 8428, logs 9428, Alertmanager 9093, session pooler 5432 and plugin HTTPS. |
| Grafana operator | Kubernetes API, Grafana 3000 and dashboard-source HTTPS. |
| vmalert, vmauth, vmsingle | Explicit metric/query/write paths, internal UIs, metrics and config-reloader endpoints; Hermes metrics and Ceph manager dashboard queries to vmsingle 8428. vmsingle's observed vmalert 8080 path is retained. |
| Alertmanager | Rule notifications, Grafana, Siren, silence operator, CrowdSec LAPI notifications and internal UI; peer mesh TCP/UDP 9094 and Discord HTTPS. |
| VictoriaLogs and collectors | Collectors ship 9428; vmauth, Grafana, internal gateway and AI ToolHive query 9428; collectors use API metadata. |
| KEDA | API access, admission 9443, aggregated API 6443, operator metrics service 9666 and configured metric queries. |
| Kepler and VictoriaMetrics operators | API reconciliation, webhook callbacks 9443 and metrics. |
| kube-state-metrics, silence operator, Siren | API object collection; API/silence reconciliation to Alertmanager; Siren UI 3000 and Alertmanager 9093 respectively. |
| Nextcloud and Satisfactory exporters | Default Nextcloud pod 80 and Satisfactory FRM 8080 respectively. |
| Prowlarr, qBittorrent, Sonarr exporters and Scraparr | Explicit configured media API ports, including Seerr's `seerr-chart` label. |
| NUT, OPNsense and Klipper exporters | Existing Flux substitution targets on TCP 3493, 443 and 80 respectively. |
| Blackbox exporter | Configured gateway TLS, k8s-gateway DNS, LAN DNS/NFS and ICMP probes. Node ICMP is scoped to host/remote-node entities. |
| Speedtest exporter | vmagent metrics; Ookla HTTP/HTTPS and observed test-server TCP 8080. |
| DRM, iGPU, power and SMART exporters | vmagent on their pod metrics ports; no other egress yet justified. SMART's Service 80 targets pod 9633. |
| node-exporter | Existing hostNetwork pods remain outside pod-policy selection; host firewall review is separate. |

There are no current CronJobs or retained Jobs in this namespace. Rare paths
still include alert webhooks, dashboard/plugin downloads, controller callbacks,
scrape discovery, remote host metrics, log metadata lookups and chart upgrades.
The collector's cross-namespace pod selector uses Cilium's documented
[namespace `Exists` mechanism](https://docs.cilium.io/en/stable/security/policy/kubernetes/#multi-cluster).

## Rollout blockers

- Capture and audit at least seven days, including all no-data exporters and
  log collectors. Empty or unknown observations do not justify their proposed
  DNS-only egress. Preserve Phase 1's executable/context drift uncertainty.
- Resolve the vmagent broad-pod exception against live ServiceMonitors and
  any future dynamic target needs. The generated sample includes an unattributed
  external TCP 9090 destination; identify it before enforcement rather than
  allowing arbitrary internet scrapes.
- Confirm secret-backed Gatus and Grafana database destinations use the intended
  pooler VIPs; no secrets were read or decrypted. Exercise Grafana plugin updates
  and Gatus route discovery after a cold restart.
- Confirm all webhook and aggregated API source identities, including control
  plane on a different node. Structural validity cannot prove Cilium NAT or
  `kube-apiserver`/`remote-node` identity matching.
- Verify remote LAN scrape targets and blackbox probes after Flux substitution,
  including the node-host paths, NFS probe and exporter targetPort translation.
- Review AI/observability and media/default exporter rules at both ends. The
  vmagent exception grants egress only; each destination still needs its own
  ingress permission.
- Recheck evaluator readiness and capture exclusions before Phase 3, keeping
  `llmBridge` enabled. The audit representation must preserve native entities,
  CIDRs, ICMP and additive rule semantics; a lossy conversion is a blocker.
  Resolve every legitimate `WouldDeny` over at least seven days and obtain owner
  approval before CNP enforcement.

## Validation

The required `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
passes full Flux/Helm rendering, shellcheck, secret guardrails and 24 guardrail
regression tests. The pinned speedtest chart was separately rendered to verify
its native policy's vmagent-only selector. Local checks cover the live CNP
structural schema, selectors against current workload labels, peer pairs and
additive ingress/egress. CIDR placeholders use documentation-only substitutes
for schema checks. These checks do not establish production reachability or a
passing audit.
