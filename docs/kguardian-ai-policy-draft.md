# AI network policy draft

Local Phase 2 proposal on `harden/kguardian-ai`, in
`.claude/worktrees/kguardian-ai`, based on `fdd3cd954`. No policy has been applied,
pushed or opened as a PR. This branch changes enforcing CNP manifests for review;
it must not be reconciled as an audit experiment.

## Evidence and limits

The 2026-10-28 read-only inventory contains 36 AI pods. kguardian Cilium policy
generation was attempted for all 36: 34 returned policies, while `karakeep-0` and
the current KV-offload guard Job returned no usable data. The namespace traffic
query returned zero records and zero pods despite those generated policies.
That is inconsistent/missing coverage, not evidence of no traffic or no drops.
Neither result proves seven continuous days of capture. The earlier triage also
reported a truncated Hermes network profile. Raw responses stay outside the
public repository; no client addresses or credentials are copied here.

Dependencies below combine generated flows with current Services, labels,
HelmRelease values, model init containers, HTTPRoutes and scheduled Jobs. Concrete
pod names and addresses from generation are replaced with stable selectors.
The write-rate branch is not an ancestor of this draft's base; no changes to
kguardian collection, evaluator or `llmBridge` are included. Refresh both live
settings and merge status before planning audit resources.

## Combined policy effect

The namespace policy establishes ingress and egress default-deny and retains
host probes, unported kube-dns reply ingress and TCP/UDP53 DNS egress. Application
grants now name peers and ports. The ToolHive file replaces its three old CNPs
with one native CNP rule list. During a future approved transition, the old
backend-wide proxy grant must be pruned: leaving it installed restores unwanted
access. No cluster-wide CNP was returned by the live inventory.

The native memini chart NetworkPolicy is disabled because its whole-namespace
grants would otherwise union with and bypass the new CNP. Memini's explicit
CNP rules take over. Inference retains its own policies; embedding-cache reaches
only embeddinggemma, and Hermes/memini reach only the named reranker directly.
There are no broad namespace-only application peers, pod-address rules, `cluster`
egress grants or FQDN/DNS-proxy rules. The existing deny-apiserver component is
unchanged; AI does not currently include it because AI hosts management tools.
Only named management roles receive kube-apiserver egress here.

| Workload / role | Proposed dependencies beyond shared DNS/probes |
| --- | --- |
| Agentgateway | Named LLM clients on 4000; headroom/self on 8080; internal gateway UI on 15000; vmagent metrics on 15020. Egress to headroom 8787, embedding-cache/inference 8080/8000, Omniroute 20128, and the network namespace's agentgateway control plane on 9978. The xDS connection is configured in the live pod, even though missing from generation. |
| Headroom / embedding-cache | Headroom accepts agentgateway and vmagent on 8787, then calls the models listener 8080. Cache accepts agentgateway on 8080 and calls only embeddinggemma on 8080. |
| Memini | Hermes/unified API 8080, internal gateway UI 8081, vmagent metrics 9090; egress to agentgateway 4000, reranker 8080 and the pgbouncer-rw pooler 5432. No direct Postgres primary VIP. |
| Hermes / OpenCode | Internal gateway UI 9119/4096; both call unified 4483 and agentgateway 4000 and retain HTTP/HTTPS internet access for browsing, provider and code/package tasks. Hermes additionally uses memini/reranker 8080, VictoriaMetrics 8428, Unpackerr's current Service target 5656, the Home Assistant Service target 8123, and observed internal gateway HTTPS 10443. |
| Unified vMCP | Named clients/internal gateway/Gatus on 4483, vmagent on 9464; proxy APIs 8080, memini/Konflate/kguardian bridge 8080, agentgateway 4000, Dragonfly 6379, internal gateway HTTPS 10443 and remote MCP HTTPS 443. Redis is configured even though absent from this generation. |
| ToolHive operator / proxies | Operator reconciles through the Kubernetes API and reaches unified/proxy health ports. Each proxy reaches the API for pod management/stdio exec, plus only its matching streaming backend. Operator metrics stay available to vmagent on 8080. |
| MCP backends | Flux uses the Kubernetes API; Ghostfolio 3333, Karakeep 3000 and SearXNG 8080 reach their matching default apps; Grafana 3000 and VictoriaLogs 9428 reach their observability apps. Home Assistant uses the selectorless external-homeassistant Service target 8123. Talos uses node entities on 50000, not labels of unrelated host-network pods. TrueNAS retains its observed internal TLS gateway route 10443. KubeSearch uses HTTPS; SearXNG URL reading uses HTTP/HTTPS. |
| LLMKube / inference | Controller API and HTTPS metadata/downloads; API-server admission ingress only on controller 9443, vmagent 8443. Model init containers need HTTPS downloads/revalidation despite empty steady-state observations. No other models become direct agent targets. |
| Scheduled / backup work | vmcp-restart's service-account selector permits API access for its scheduled restart. Kopiur movers retain API access; filesystem backup NFS mounts are performed by the node. Daily compile-cache prune and 15-minute KV guard operate on local volumes and receive no additional network grants. |

The SearXNG HTTP/HTTPS exception is supported by its
[upstream URL-reading tool](https://github.com/rcdailey/mcp-searxng#tools).
The restart Job uses Cilium's native
[service-account selector](https://github.com/cilium/cilium/blob/main/examples/policies/kubernetes/serviceaccount/serviceaccount-policy.yaml).
`world` grants are coarse protocol allowances, not a destination-domain allowlist.
They still require owner review and application-layer controls.

## Questions blocking audit approval or enforcement

- Attribute Hermes's observed SSH and unresolved 1925/4096 connections to tasks
  and stable destinations. The existence of OpenCode's 4096 Service does not
  prove that the observed destination was OpenCode. No catch-all SSH/LAN grant
  or inferred Hermes-to-OpenCode permission is added.
- Confirm the old Unpackerr 16373 observation is stale and exercise the current
  5656 integration. Check Home Assistant access through its selectorless Service
  against the live endpoint and Cilium policy evaluation.
- Attribute the shared HTTPS destination observed for the ToolHive operator and
  proxies. It does not match the current Kubernetes Service IP; configured
  telemetry is Prometheus pull. API access is retained, but unproven internet
  access is not silently added. No external reverse lookup was performed.
- Explain Headroom's historical HTTPS traffic against `HEADROOM_OFFLINE=1`.
  Its draft permits its configured gateway dependency; resolve any legitimate
  cold-start or maintenance internet path before audit approval.
- Obtain usable Karakeep and KV-guard observations, exercise all MCP transports,
  browser/code tasks, model cold starts, scheduled Hermes tasks and backup
  restore/mover paths. Retained prestage/prepull Jobs are historical evidence,
  not a reason to give every future Job internet access.
- Verify at least seven days of continuous relevant capture and later audit
  verdicts, including the daily prune, 15-minute guard, selected-day vMCP restart
  and nightly Hermes/Omniroute/OpenCode movers. Missing/null data remains unknown.
- Before any deployment, refresh selectors, overlapping policies, evaluator
  availability and kguardian branch/live configuration. Stage audit CRs only
  after the owner approves that separate phase, then resolve every legitimate
  WouldDeny. The Cilium service/node/entity semantics used here must be checked
  against the evaluator's supported model; an unsupported dimension cannot be
  counted as a passing audit.

## Local validation

- Repository `validate-pr.sh` via mise: full Flux/Helm rendering, shellcheck and
  secret guardrails/regression checks.
- Current Cilium CRD and published CNP schema validation; live inventory matching
  for every peer/service selector and baseline coverage for all 36 pods.
- Additive ingress checks cover valid Hermes-to-unified and cache-to-embedder
  paths, and denied direct agent-to-chat, cross-proxy-to-backend and unrelated
  app-to-app paths. These are static checks, not dataplane proof.
- Rendered memini contains no native NetworkPolicy; its broad additive grant is
  removed. No securityContext values or application credentials changed.
