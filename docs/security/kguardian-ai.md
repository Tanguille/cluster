# AI security hardening decisions

Draft based on main `5f33d7baf`, with the AI network-policy proposal from PR #6265
head `2535e0525`. Branch `harden/kguardian-ai-security`; worktree
`.claude/worktrees/kguardian-ai-security`. These are local proposed changes, not
live rollout results. Network enforcement and any custom seccomp binding still
require owner approval and the separate audit gates.

## Evidence and interpretation

All 38 current AI controller profiles and their latest revision diffs were read
on 2026-10-28, alongside live pod specifications and repository configuration.
The evidence includes three CronJobs, ten MCP backend StatefulSets and 25
Deployments. Fresh data can still be partial or stale: completion of a collector
request does not establish continuous capture or recent execution of every Job.

No `unshippedExecutable` or `securityContextRegression` finding was returned.
Neither check proves clean: executable comparisons lack inventory/running
containers, and context regression has `no_baseline` for 30 workloads and
`no_container_data` for eight. All 38 have no export for image/export comparison;
37 evaluated tag movement and one lacks image inventory. The two security-related
revision diffs remove container inventory from completed vmcp-restart and
compile-cache-prune Jobs. They do not establish a configuration regression.
Investigate any future executable/context finding before advancing rollout.

Every returned PSS level below is an **upper bound**, never a compliance claim.
The broker evaluates only 9 of 18 PSS checks where container inventory exists.
HostProcess, hostPath/volume types, host ports/probe hosts, AppArmor, SELinux,
procMount and sysctls remain unevaluated. `null` means no PSS data. A reported
`restricted` upper bound can coexist with unknown podSecurity in overall posture.
Image provenance, signatures, SBOMs and vulnerabilities are unassessed; missing
inventory is not a clean image verdict. The syscall warning concerns absence of
a custom SeccompProfile CR and does not mean an existing RuntimeDefault is absent.

## Workload decisions

“Applied” means included in this draft only. “Already” records manifest/operator
settings without adding redundant values. “Exempt” preserves a documented runtime
or API requirement. “Deferred” identifies work that needs further evidence.
Coverage and unknownDimensions are copied literally from each returned posture.

| Kind / workload | Posture; coverage; unknownDimensions | PSS upper bound | Decision | Evidence and scope |
| --- | --- | --- | --- | --- |
| CronJob / vmcp-restart | warn; 0.25; `[network, podSecurity, images]` | null | Already / exempt | Manifest already sets non-root, RuntimeDefault, no escalation, read-only root and drop ALL. API token required for rollout restart; current profile has no container inventory. |
| CronJob / qwen38-27b-vllm-kv-offload-guard | risk; 0.5; `[network, images]` | baseline | Applied / exempt | Add RuntimeDefault and disable unused API token. Keep UID 0 for root-owned filesystem maintenance; existing read-only root, no escalation and drop ALL remain. |
| CronJob / qwen38-27b-vllm-compile-cache-prune | warn; 0.25; `[network, podSecurity, images]` | null | Applied / exempt | Add RuntimeDefault and disable unused API token. Keep UID 0 for root-owned filesystem maintenance; existing read-only root, no escalation and drop ALL remain. |
| StatefulSet / victorialogs-mcp | warn; 0.25; `[network, podSecurity, images]` | restricted | Already | Token already disabled; existing context and writable /tmp exception retained. |
| StatefulSet / truenas | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / talos-mcp | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / searxng | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / kubesearch | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / karakeep | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / homeassistant | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / grafana | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / ghostfolio | warn; 0.25; `[network, podSecurity, images]` | restricted | Applied | Disable unused API token; retain operator-provided non-root, no-escalation, dropped capabilities, read-only root and RuntimeDefault. Live pod had a projected API token. |
| StatefulSet / flux-operator | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Context already set. Preserve API credentials: explicit KUBECONFIG exists, but its secret-backed authentication mechanism was not inspected. |
| Deployment / victorialogs-mcp | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / unified | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Operator context already set; retain API discovery credentials and writable /tmp for the FTS index. |
| Deployment / truenas | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / toolhive-operator | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Controller context already set; API token is required for reconciliation. |
| Deployment / talos-mcp | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / searxng | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / qwen38-27b-vllm | risk; 0.5; `[network, images]` | baseline | Already / exempt | RuntimeDefault already documented working with ROCm. Preserve UID 0/GPU groups, cache-prep capabilities and writable cache/runtime; related CronJob changes have separate rows. |
| Deployment / qwen35-2b | risk; 0.5; `[network, images]` | baseline | Applied / exempt | Add native pod RuntimeDefault. Retain root/GPU groups, cache-prep CHOWN/FOWNER and writable downloader/runtime; no supported token override added speculatively. |
| Deployment / opencode | warn; 0.5; `[network, images]` | baseline | Applied / deferred | Add no escalation and RuntimeDefault. Keep gVisor; defer non-root UID, capability removal and read-only root until image/PVC ownership and agent tools are verified. |
| Deployment / omniroute | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / deferred | Existing UID/GID 1000, non-root, RuntimeDefault, no escalation and drop ALL retained. Read-only root requires verified writable runtime/cache paths. |
| Deployment / memini | warn; 0.5; `[network, images]` | baseline | Applied | Add pod RuntimeDefault; chart-rendered UID 65532, non-root, no escalation, read-only root and drop ALL are preserved. |
| Deployment / kubesearch | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / llmkube-controller-manager | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Controller context already set; API token is required for reconciliation. |
| Deployment / karakeep | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / hermes | warn; 0.5; `[network, images]` | baseline | Exempt / already | Preserve root/s6 permission setup and capability set; RuntimeDefault, gVisor and no escalation already set. Hardened google-digest sidecar remains unchanged. |
| Deployment / homeassistant | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / headroom | warn; 0.5; `[network, images]` | baseline | Applied | Add pod RuntimeDefault; retain existing UID/GID 1000, no escalation and drop ALL. Writable filesystem remains pending cold-start/cache validation. |
| Deployment / grafana | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / ghostfolio | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / flux-operator | warn; 0.25; `[network, podSecurity, images]` | restricted | Already / exempt | Proxy context already set by ToolHive. Preserve API token for backend pod management and stdio exec/attach; backend token decisions are separate rows. |
| Deployment / embeddinggemma-2 | risk; 0.5; `[network, images]` | baseline | Applied / exempt | Add native pod RuntimeDefault. Retain root/GPU groups, cache-prep CHOWN/FOWNER and writable downloader/runtime; no supported token override added speculatively. |
| Deployment / embedding-cache | warn; 0.25; `[network, podSecurity, images]` | restricted | Already | Existing UID/GID 101, RuntimeDefault, no escalation, read-only root, drop ALL and writable cache mounts retained. |
| Deployment / agentgateway | warn; 0.5; `[network, images]` | baseline | Applied / deferred | Add RuntimeDefault through AgentgatewayParameters deployment fields; preserve generated container context and discovery credentials. Missing memory-limit finding needs capacity evidence, not an arbitrary limit. |
| Deployment / bench-lfm3-17b | risk; 0.5; `[network, images]` | baseline | Deferred | Observed benchmark controller has no matching GitOps workload in this checkout. Establish owner/source and cache/runtime requirements before changing it. |
| Deployment / bge-reranker-v2-m3 | risk; 0.5; `[network, images]` | baseline | Applied / exempt | Add native pod RuntimeDefault. Retain root/GPU groups, cache-prep CHOWN/FOWNER and writable downloader/runtime; no supported token override added speculatively. |

## What changes and what stays an exception

The context changes add RuntimeDefault to four application pods, three llama
InferenceServices and two maintenance CronJobs. OpenCode additionally refuses
privilege escalation; its direct `opencode web` command has no configured
privileged startup step. UID, capabilities, mounted filesystems and container
commands are preserved. The existing vLLM RuntimeDefault setting provides local
ROCm precedent; cold-start and inference testing remain required for each llama
runtime before owner-approved deployment.

Eight MCP backend pods and the two filesystem-only CronJobs stop automounting the
Kubernetes API token. The live inventory confirms a projected service-account
token in each of those eight backends; VictoriaLogs already disables it and is
unchanged. ToolHive proxies and controllers keep API access. Flux MCP retains its
token pending verification of its mounted kubeconfig's authentication mechanism;
no secret was read or decrypted. The backend settings use MCPServer's native
`podTemplateSpec`, leaving proxy deployments and their credentials untouched.

Hermes still starts as root for s6 and permission setup before dropping the
gateway process to its application user. Removing its UID/capabilities or making
its root filesystem read-only is not justified by sampled syscall data. GPU
models still require a separate ownership/device review before changing root,
cache-preparation capabilities or writable runtime paths. API-token findings on
controller-created inference pods are deferred until a supported native override
and controller behavior are verified. The namespace's privileged PSA exception
is unchanged; no PSS restricted-compliance claim follows from these edits.

## Validation and rollout gates

Local validation passed:

- `flate build hr` rendered Headroom, memini and OpenCode. Assertions confirmed
  the effective contexts, memini's retained chart defaults, OpenCode's unchanged
  command, gVisor and writable PVC, and existing disabled API-token defaults.
- Kustomize builds and assertions covered four InferenceServices, two maintenance
  CronJobs, ten MCPServer templates and AgentgatewayParameters. GPU groups, UID 0,
  existing mounts and API credentials for the controller/proxy paths are retained.
  Read-only live CRD inspection confirmed the native field paths.
- The source semantic diff contains exactly nine new RuntimeDefault fields, ten
  token-disable fields and one no-escalation field. No unrelated source value
  changed; existing staged namespace network changes were the comparison base.
- The required `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
  passed full Flux/Helm rendering, shellcheck, secret guardrails and 25 regression
  tests. All 38 controller profiles have a decision row above.

Runtime verification is still required:

- Before enforcement, test cold model downloads/loads, inference and probes,
  OpenCode plugin/tool execution, each MCP transport and both maintenance Job
  schedules. Ensure removal of the backend token does not expose an undocumented
  API dependency. Token removal does not revoke other explicit service secrets.
- Preserve network audit gaps and at least seven days of usable capture/verdicts.
  The feature evidence below is review material, not resources reconciled by
  Flux. Unsupported Cilium audit dimensions must not
  be counted as coverage. Seccomp generation has a reported architecture-mapping
  blocker despite x86_64 raw syscall evidence; this draft adds no custom profile
  or Localhost binding and retains existing RuntimeDefault protection.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 38 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 0 records, 0 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 24 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 24 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `hermes` pod returned 72 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 37 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
| Audit verdicts | WouldDeny query empty; live inventory contains no AuditNetworkPolicy/AuditClusterNetworkPolicy CRs. No audit has started or passed. |

The MCP `generate_seccomp_profile` call returned `unrecognized architecture —
no seccomp arch mapping (supported: x86_64, aarch64)`, even though
`get_pod_syscalls` reports x86_64 for the same pod. Treat this as an export blocker.
Keep RuntimeDefault; do not replace it with an unverified allowlist or LOG profile.
A workload aggregate can include older pods and differs from this single-pod set.
Resolve the exporter and capture startup, Jobs and rare paths before proposing
a custom profile; no generated syscall set is claimed complete over seven days.

No `unshippedExecutable` or `securityContextRegression` drift item was returned.
Executable inventory and a context/export baseline are missing for these checks;
that is unknown, not a clean drift result. Investigate any future positive finding
before advancing enforcement.

### Revision evidence

Latest retained revision pairs were compared, not a seven-day continuous history.
Peer addresses are deliberately omitted. New ports require attribution; an added
HTTPS peer alone does not identify its hostname or justify a broader grant.

| Workload | Revisions | Changed dimensions | Network observations |
| --- | --- | --- | --- |
| CronJob / qwen38-27b-vllm-compile-cache-prune | 6 → 7 | images, podSecurity | No peer changes |
| CronJob / qwen38-27b-vllm-kv-offload-guard | 955 → 956 | images | No peer changes |
| CronJob / vmcp-restart | 8 → 9 | images, podSecurity | No peer changes |
| Deployment / agentgateway | 21 → 22 | network | 3 removed: egress TCP/8787, ingress TCP/15020, ingress TCP/8080 |
| Deployment / bench-lfm3-17b | 3 → 4 | images | No peer changes |
| Deployment / bge-reranker-v2-m3 | 75 → 76 | images | No peer changes |
| Deployment / embedding-cache | 1 → 2 | syscalls | No peer changes |
| Deployment / embeddinggemma-2 | 44 → 45 | images | No peer changes |
| Deployment / flux-operator | 26 → 27 | images | No peer changes |
| Deployment / ghostfolio | 23 → 24 | images | No peer changes |
| Deployment / grafana | 33 → 34 | images | No peer changes |
| Deployment / headroom | 236 → 237 | network | 1 removed: egress TCP/443 |
| Deployment / hermes | 229 → 230 | network | 1 removed: egress TCP/443 |
| Deployment / homeassistant | 21 → 22 | images | No peer changes |
| Deployment / karakeep | 22 → 23 | images | No peer changes |
| Deployment / kubesearch | 28 → 29 | images | No peer changes |
| Deployment / llmkube-controller-manager | 98 → 99 | images | No peer changes |
| Deployment / memini | 23 → 24 | network | 1 added: egress TCP/4000 |
| Deployment / omniroute | 98 → 99 | network | 1 added: egress TCP/443 |
| Deployment / opencode | 33 → 34 | network | 1 added: egress TCP/443 |
| Deployment / qwen35-2b | 85 → 86 | images | No peer changes |
| Deployment / qwen38-27b-vllm | 142 → 143 | images | No peer changes |
| Deployment / searxng | 23 → 24 | images | No peer changes |
| Deployment / talos-mcp | 29 → 30 | images | No peer changes |
| Deployment / toolhive-operator | 26 → 27 | images | No peer changes |
| Deployment / truenas | 22 → 23 | images | No peer changes |
| Deployment / unified | 51 → 52 | images | No peer changes |
| Deployment / victorialogs-mcp | 27 → 28 | images | No peer changes |
| StatefulSet / flux-operator | 8 → 9 | syscalls | No peer changes |
| StatefulSet / ghostfolio | 9 → 10 | images | No peer changes |
| StatefulSet / grafana | 21 → 22 | images | No peer changes |
| StatefulSet / homeassistant | 18 → 19 | syscalls | No peer changes |
| StatefulSet / karakeep | 12 → 13 | network | 1 removed: egress TCP/3000 |
| StatefulSet / kubesearch | 17 → 18 | syscalls | No peer changes |
| StatefulSet / searxng | 38 → 39 | network | 1 removed: egress TCP/443 |
| StatefulSet / talos-mcp | 12 → 13 | network, syscalls | 2 added: egress TCP/50000, egress UDP/53 |
| StatefulSet / truenas | 9 → 10 | network | 1 added: egress TCP/10443 |
| StatefulSet / victorialogs-mcp | 6 → 7 | syscalls | No peer changes |

### Feature prerequisites and audit gate

Pinned kguardian chart 1.29.2 defaults and live settings were inspected.
PR #6264 (`fix/kguardian-write-rate`) remains open and unmerged at this check;
evaluator and compute are enabled, syscall capture is full, and `llmBridge` remains
enabled. Recheck before starting an audit: that proposal disables the evaluator
and excludes additional namespaces. Do not enable expensive capture implicitly.

The installed AuditNetworkPolicy/AuditClusterNetworkPolicy schemas use native
Kubernetes NetworkPolicy peers/selectors/ports. They cannot faithfully express
Cilium entities, service-account identities, selectorless Services, ICMP or DNS/L7
restrictions and Cilium deny precedence. A lossy conversion would produce false
confidence. No purported equivalent audit CR is included. Establish equivalent
evaluation or independently test unsupported dimensions before approval.

Supply-chain ingestion is off. In this chart it requires scoped broker auth with
a supplychain token and an actual report source (for example Trivy Operator; a
Trivy server alone does not supply operator reports). Signature discovery and
image-trust evaluation are separate opt-ins. Runtime executable inventory is
`controller.runtimeInventory.mode: off`, with capability tracing also off; enabling
them needs an overhead/write-rate check and owner approval. No secrets or global
kguardian settings are changed by this namespace PR.

Before merging the enforcing CNPs: refresh live settings and workloads; obtain
approval for the audit deployment; collect at least seven complete days including
the rare paths below; fix every legitimate WouldDeny with evidence; validate the
unsupported Cilium dimensions; then obtain explicit enforcement approval. Watch
drop deltas and app health after an approved rollout. Static rendering and an
empty query are not runtime validation.

## Network policy and rare-path review

[Namespace](../../kubernetes/apps/ai/networkpolicy.yaml),
[ToolHive](../../kubernetes/apps/ai/toolhive/app/networkpolicy.yaml) and
[inference](../../kubernetes/apps/ai/llmkube/models/networkpolicy.yaml) rules preserve
named model/MCP clients, matching proxy backends, API management roles, model init
downloads and scheduled/mover work. Agentgateway xDS 9978 and Unified's configured
Redis dependency are included despite absent samples. Memini's broad native
policy is disabled; embedding-cache reaches only its embedder and agent direct
inference access stays limited to the named reranker.

Resolve Hermes SSH/1925/4096 attribution, Headroom HTTPS despite offline mode,
and shared ToolHive operator/proxy HTTPS that is not the Kubernetes Service IP.
No guessed SSH/LAN/internet grants were added. Karakeep and the KV-guard Job had
no data. Audit every MCP transport, Home Assistant's selectorless
Service, node-based Talos access, browser/code tasks, cold models and their pod-wide
`world` 443 (init downloader), Hermes schedules,
cache pruning, vMCP restart and nightly movers. Detached Paseo is not active and
needs its own policy/evidence before activation; it does not inherit Hermes rules.

Final review found and repaired one missing dependency: Unified's live config
uses `groupRef: all` and `outgoingAuth.source: discovered`. Its service account
is bound to get/list/watch MCP resources, configuration and discovery credentials,
and patch VirtualMCPServer status. Add Kubernetes API TCP 443/6443 to Unified's
existing app/instance selector; the operator/proxy selectors do not cover it.
This narrow rule fixes a draft omission; no other agent gains API access.

## Trivy follow-up, 2026-10-28

The [image ledger](trivy-ai-images.csv) records 26 distinct current or scheduled
image digests: 13 have matching reports and 13 have **unknown** vulnerability
contents. The [finding ledger](trivy-ai-findings.csv) preserves all 408 distinct
Critical/High image/package findings (12 Critical, 396 High), including installed
versions and every reported fixed version. These are existing report findings,
not a new scan or the number of distinct CVE identifiers. Repeated pods and report
owners are deduplicated by image digest, finding id, package and version.

Read-only Flux MCP queries refreshed pod images, init-container imageIDs, report
metadata and RBAC bindings. AI is excluded from current Trivy capture, so old
reports remain evidence of known defects, not current coverage. Most MCP backend
reports are 4–16 days old. The 34-day-old `curlimages/curl:8.18.0` report belongs
to an obsolete Job, but its exact digest still matches current LLMKube init
containers. An owner-only join would incorrectly discard its 2 Critical and
24 High findings. The recent BusyBox report from another namespace matches the
exact scheduled/current digest. No report is counted as clean merely because its
owner disappeared; no missing report is counted as zero.

Two publisher-image updates are drafted:

| Consumer | Candidate | Evidence and limits |
| --- | --- | --- |
| Flux MCP backend | `ghcr.io/controlplaneio-fluxcd/flux-operator-mcp:v0.62.1@sha256:0b4181bc9ada80eedf3e9df3355153abc9c3e6d8b913bddebe0b12908309bff6` | Reading the exported binary with `go version -m` found actual Go 1.27.3 and `golang.org/x/net` v0.61.0, meeting the fix versions in all four existing High records. No image or binary was executed. |
| LLMKube model initialization | `docker.io/curlimages/curl:8.23.0@sha256:11e64e28c5f58c8d93ee95fcf22ac055f48b0bc440e0ce7d8013bbb5ad6aa8ac` | The amd64 image's APK database has libcrypto3/libssl3 3.5.9-r0, libssh2 1.11.1-r4, musl/musl-utils 1.2.6-r2, nghttp2-libs 1.70.0-r0 and zlib 1.3.2-r1; nghttp2-dev is absent. These meet the 26 historical package fix criteria or remove the affected package. |

Flux MCP's OCI revision matches
[the inspected source](https://github.com/controlplaneio-fluxcd/flux-operator/blob/48e2cd76c475be87a1c9a8bd83fe07e064abd725/cmd/mcp/main.go):
`serve --transport=http --port=8080`, `/mcp`, KUBECONFIG and the nonroot entrypoint
remain supported. No tool filters, credentials or permissions change.
LLMKube uses the pinned chart's native `controllerManager.initContainer` values;
the chart renders the override into `--init-container-image`. Current model
pod/initialization identities, CHOWN/FOWNER cache preparation and download flags
remain unchanged. The candidate archive contains the existing scripts' shell,
curl, flock, stat, find, hash and filesystem utilities. Static package/command
inspection does not test cold downloads, cache revalidation or filesystem locks.
The evidence covers amd64, the current model/MCP node architecture; another
architecture needs separate artifact validation.

The other backend stable tags still resolve to the reported digests. Publisher
release checks and blockers are recorded per image. Talos MCP's stable 0.0.2 is
unchanged; its unversioned latest image was built on 2026-07-16 and has no verified
fix set. Kubernetes' latest stable kubectl 1.37.2 still has the two reported
dependencies. No unverified digest refresh is called a fix.

Searxng's unchanged fork is still package version 0.10.0. The
[official 2.9.0 source](https://github.com/ihor-sokoliuk/mcp-searxng/blob/59635c3a4b3cca5aafc183570be3f56a6032dcdc/package.json)
is a migration candidate, with newer MCP SDK/undici/Hono dependencies, but is
deferred: its HTTP default bind is loopback, it validates Origin, and authentication,
session handling, tool inventory and result contracts differ. The
[HTTP guide](https://github.com/ihor-sokoliuk/mcp-searxng/blob/59635c3a4b3cca5aafc183570be3f56a6032dcdc/docs/http-server.md)
requires explicit deployment choices. Keep a versioned candidate, exercise
ToolHive discovery, `/health`, search and URL-read contracts, and obtain a fresh
scan before replacing the fork. No authentication bypass is added.

The config/RBAC review retains the following explicit blockers; none is ignored
or removed from Trivy:

| Finding | Evidence and decision |
| --- | --- |
| AVD-KSV-0014 / AVD-KSV-0118 on old prestage/prepull Jobs | These config reports do not match active workload owner UIDs. They do not establish the current pod's security context. The Curl image finding above still applies independently by digest. |
| AVD-KSV-0056 on ten ToolHive proxy runner Roles | MCP confirms these Roles manage backend StatefulSets and Services. Removing Service create/update/delete breaks the proxy's backend lifecycle. Keep the necessary capability; narrower operator-generated RBAC needs an upstream-supported design and lifecycle tests. |
| AVD-KSV-0041 / 0050 / 0056 on `llmkube-manager-role` | Model credentials, generated Services and controller-managed RBAC need these permissions. Keep the controller's functioning permissions; any namespace/feature restriction requires separately tested operator support. |
| AVD-KSV-0041 / 0046 / 0050 / 0056 on `toolhive-operator-manager-role` | MCP confirms the active binding. MCPServer/VirtualMCPServer reconciliation creates credentials, proxy identities and network resources. Do not remove API watches or managed-resource permissions to clear the scanner. |

The 30 candidate-addressed ledger rows are **not deployed or rescanned**. The
remaining findings and unknown images still need fresh reports and patched
publisher artifacts, or an explicitly reviewed migration. Network restrictions
do not repair vulnerable packages, and none of these results authorizes enforcing
the network-policy draft.
