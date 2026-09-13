# ToolHive GitHub Policy Implementation Plan — BLOCKED at source-validation gate

> **For agentic workers:** REQUIRED SUB-SKILLS: use `executing-plans` or `subagent-driven-development`, plus `verification-planning`. Steps use checkboxes. Do not execute configuration tasks until Gate A is closed with evidence. No automatic commits, pushes, reconciles, GitHub writes, or Secret access are authorized by this document.

**Goal:** Restrict GitHub mutations through unified to all Tanguille-owned repositories and valid explicit branches other than `main`/`master`, allow owned-target normal/draft PR creation and explicit public read tools, and deny unrelated mutations.

**Architecture:** Keep unified optimized and anonymously shared. Replace its `github` entry with a private-endpoint reference to a non-optimized Cedar `github-policy` VMCP, backed only by the isolated `github-upstream` group and hosted entry; inject the existing PAT only at that raw entry. This is logical routing isolation, not network isolation against every cluster client.

**Tech Stack:** ToolHive operator/CRDs/VMCP v0.49.0, Cedar, Flux/Kustomize, hosted GitHub MCP, Go integration tests using the actual pinned ToolHive runtime and loopback mock backends.

---

## Status and mandatory stop

This is a **blocked verification-first plan**, not an implementation-ready YAML recipe. Source inspection uncovered an authorization ambiguity that prevents honestly providing a complete proven policy now. Do not fabricate a discovery-only Cedar condition, treat required JSON Schema properties as automatically enforced, or weaken the approved missing-argument requirement to make discovery pass.

Approved input: `docs/superpowers/specs/2026-09-13-toolhive-github-policy-design.md` (read in full). Although its document status still says proposal, the user explicitly approved it. The planning session made no manifest changes and did not run the runtime experiment below.

### Gate A: discovery and empty invocation are indistinguishable to Cedar

Verified v0.49.0 source:

1. `pkg/vmcp/core/admission.go`, `cedarAdmission.FilterTools`: discovery authorizes each native tool with `MCPFeatureTool`, `MCPOperationCall`, its name/annotations and **nil arguments**.
2. The same file, `AllowToolCall`: invocation uses the same feature/operation/name/annotations with actual arguments.
3. `pkg/authz/authorizers/cedar/core.go`, `preprocessArguments`: scalar arguments become `arg_<name>`; other values, including null/objects/arrays, become `arg_<name>_present: true`. Nil and empty argument maps produce no argument attributes.
4. `authorizeToolCall` merges those attributes into **both resource attributes and Cedar context**, with `Tool::<name>` / `Action::call_tool`. There is no discovery-vs-invocation discriminator in the inspected conversion.
5. `pkg/vmcp/core/core_calls.go`, `CallTool`: authorizes, checks the advertised aggregation view, routes, derives parameter headers, and calls the backend. The inspected function does **not** perform general input-schema validation. Parameter-header derivation is not general schema validation.

Consequently a policy that makes a mutation discoverable by permitting an argument-free decision also permits that decision for an empty-argument invocation. A strict required-argument policy instead hides the mutation during discovery. **Cedar alone does not establish both requirements.** Whether another real transport/backend validation boundary reliably rejects empty invocations remains unverified.

Smallest already-approved omission: leave `create_or_update_file` out entirely. Its optional branch default is unnecessary risk. This omission does **not** resolve the empty-argument ambiguity for `push_files`, `create_branch`, or `create_pull_request`.

The user allows reliance on actual required-argument validation only if proven. A mock programmed to reject missing arguments proves the mock, not the hosted service. If no real pinned validation boundary can be exercised without real GitHub writes, stop and return the blocker; request a design revision rather than claiming the policy solved it.

### Other source findings and open checks

| Subject | Evidence / decision |
|---|---|
| Version | Repository `kubernetes/apps/ai/toolhive/app/ocirepository.yaml:12–14` now pins `0.49.0`; do not use earlier 0.47.1 findings as current runtime proof. |
| Authz placement | `cmd/thv-operator/api/v1beta1/virtualmcpserver_types.go:176–205`: authz belongs at **`spec.incomingAuth.authzConfig`**, not `spec.authz` or `spec.config.authz`. Inline fields are `type`, `inline.policies`, `inline.entitiesJson`; `authzConfigRef` is a mutually exclusive alternative. Confirm accepted type enum and controller conversion before serialization. |
| Optimizer incompatibility | `pkg/vmcp/server/server.go`, `New`: `Config.Authz` and `Config.OptimizerConfig` are mutually exclusive. Configure authz only on inner VMCP. Use `server.New`, not direct `Serve`, in the fixture: the latter cannot substitute for authorization composition. |
| Private entry | `mcpserverentry_types.go` declares `remoteUrl` and `allowPrivateEndpoint`. Approved exact URL: `http://vmcp-github-policy.ai.svc.cluster.local:4483/mcp`; `server.go` default endpoint is `/mcp`. Operator-generated Service naming still needs render/converter evidence. |
| Aggregation | Pinned generated VirtualMCPServer CRD contains `config.aggregation.conflictResolutionConfig.priorityOrder` and `tools[].workload/filter`. The filter is an advertisement allowlist; routing retains hidden tools for composites. `core_calls.go` adds a direct-call advertised-view check. No composites/code mode are to be configured. Prove native name preservation through the real nested hop. |
| Header shape | Pinned `examples/operator/mcp-server-entries/mcpserverentry_with_header_forward.yaml` uses `spec.headerForward.addPlaintextHeaders` as a header-name-to-string map. |
| Header semantics NOT established | Official hosted docs describe `X-MCP-Tools` as comma-separated tools to **enable**, and say empty `X-MCP-Toolsets` selects defaults. They do not establish that `X-MCP-Tools` alone disables all defaults. Do not label it an exclusive allowlist until exact enable semantics are proven. An empty toolsets header is not a proven fix. |
| Nesting | No real two-hop VMCP test ran during planning. Source support for individual components does not prove integration. |

## Verification ownership and budget

| Claim | Owner during implementation | Minimum decisive evidence |
|---|---|---|
| Pinned field/conversion/attribute semantics | Implementer | Source ledger with tag commit, exact symbols/lines and generated CRD paths |
| Discovery and fail-closed calls coexist | Implementer | Real v0.49.0 HTTP runtime test with a permissive counting backend; no mock authorization |
| Required-argument fallback, if necessary | Implementer | Test the actual enforcement implementation; a handwritten validating mock is insufficient |
| Nested prefix, routing, policy, failure behavior | Implementer | Same fixture extended to two real VMCPs, actual optimizer and loopback embedding service |
| Rendered configuration / standard checks | Implementer | Manifest assertions and `validate-pr.sh` |
| Approved spec coverage and permission gates | Parent/reviewer | Review evidence ledger, complete diff, negative-test matrix, and limitations |

Run source checks once per pinned revision; rerun integration when policy, schemas, runtime, topology, or allowlist changes. YAML validation is necessary but cannot establish runtime authorization. Stronger fallback: real operator conversion/envtest plus the runtime fixture. Weaker Cedar-only truth tables are useful diagnostics, not acceptance evidence.

## Task 1 — Resolve source contradictions before any manifests

**Read:** approved spec; root/Kubernetes `AGENTS.md`; `.agents/learned-preferences.md`, `.agents/learned-workspace.md`, `.agents/common-operations.md`; `.mise.toml`; existing ToolHive manifests.

**Research assets only:** an approved isolated worktree or `/tmp/opencode/toolhive-github-policy-research/`. Load `git-worktree-isolation` before creating a worktree. Check parent directory and destination absence before cloning. Do not overwrite existing research or the approved original spec.

- [ ] Repeat `git status --short --branch && git pull --ff-only && git status --short --branch`; preserve unrelated untracked paths. Confirm `git rev-parse HEAD`. Do not assume the parent's commit remains current.
- [ ] Clone pinned source into the verified unused research location with `git clone --depth 1 --branch v0.49.0 https://github.com/stacklok/toolhive.git /tmp/opencode/toolhive-github-policy-research`. Record `git rev-parse HEAD` from that directory. No edits to the cluster tree.
- [ ] Inspect these exact source paths, including their existing tests:
  - `cmd/thv-operator/api/v1beta1/virtualmcpserver_types.go`
  - `cmd/thv-operator/api/v1beta1/mcpserver_types.go`
  - `cmd/thv-operator/api/v1beta1/mcpserverentry_types.go`
  - `deploy/charts/operator-crds/files/crds/toolhive.stacklok.dev_virtualmcpservers.yaml`
  - `deploy/charts/operator-crds/files/crds/toolhive.stacklok.dev_mcpserverentries.yaml`
  - `pkg/vmcp/core/admission.go`, `core_calls.go`, `core_checks.go`
  - `pkg/authz/authorizers/cedar/core.go`, `entity.go`
  - `pkg/vmcp/server/server.go`, `authz_integration_test.go`
  - `examples/operator/mcp-server-entries/mcpserverentry_with_header_forward.yaml`
- [ ] Record exact authz enum, inline policy conversion, priority-name behavior, filter enforcement and Secret header forwarding into runtime config. Verify the operator's Service construction yields `vmcp-github-policy:4483`; verify `/mcp`, not root-only assumptions.
- [ ] Obtain hosted read-only `tools/list` through an already-authorized route that does not require extracting credentials. Save only tool names/schemas/annotations, no headers/tokens. If that route cannot safely exercise header selection, leave header exclusivity unverified and stop the corresponding gate. Do not use live `tools/call` mutations even with intentionally invalid arguments.
- [ ] Establish authoritative `X-MCP-Tools` enable/default interaction, including combination with `X-MCP-Toolsets` and endpoint paths. Produce the exact selected set; test no implicit default additions. Header enablement alone is not authorization.
- [ ] Select exact public-read native names from that captured list. No wildcards or blanket `readOnlyHint` permits. Review multi-method tools and exclude any mixed mutation method. Final mutation candidates are only `create_branch`, `push_files`, `create_pull_request`; omit `create_or_update_file`.

**Expected evidence:** source ledger and exact hosted schema fixture. **Stop:** cannot distinguish discovery safely or cannot prove required-argument fallback; cannot establish exact header selection; or source contradicts approved topology. Do not move on by writing speculative Cedar/YAML.

## Task 2 — Build the decisive real-runtime fixture (research only)

**Proposed isolated files:** add `pkg/vmcp/server/github_policy_security_test.go` in the pinned ToolHive research checkout, plus `pkg/vmcp/server/testdata/github-policy-tools.json` containing nonsecret captured schemas. These are not approved production changes.

- [ ] Adapt the pinned `authz_integration_test.go` setup, using its actual `server.New` composition, real Cedar authorizer, actual aggregation/client/session components and HTTP handler. Do not reimplement owner/branch checks in Python/Go and call that the policy under test.
- [ ] Add a loopback MCP backend with the captured tool schemas. Every received `tools/call` increments an atomic per-tool counter and returns a harmless marker; it must initially accept all argument values. It must have no GitHub client, credential, upstream URL, or write implementation. Its permissiveness detects gateway leakage.
- [ ] Deny outbound HTTP except loopback in the fixture transport; do not load cluster config, Secrets, process GitHub credentials or Git credential helpers. Refuse any non-loopback backend before starting a test. Exercise only temporary listeners.
- [ ] First prove fixture sensitivity: no-authz control advertises a mutation and increments the counter on `{}`. Then a deny-all real Cedar configuration must reject the same call with counter unchanged. If either control fails, fix the fixture rather than interpreting policy results.
- [ ] Add `TestGitHubPolicyDiscoveryEmptyArguments`: require permitted mutation names visible in argument-free discovery, then invoke each with absent arguments, null arguments and `{}`. Require explicit failure. Record whether any backend call occurred and which real component rejected it.
- [ ] Run from the source checkout: `go test ./pkg/vmcp/server -run '^TestGitHubPolicy' -count=1 -v`. Expected initial security test may fail; preserve the counter evidence. No production manifest may be written on that failure.
- [ ] If a discovery permit leaks empty calls to the permissive backend, evaluate only the user's allowed required-schema fallback. Exercise the actual pinned implementation responsible for rejecting missing required fields; never add rejection logic to the mock and present that as hosted-service proof. State separately “gateway rejected before forwarding” versus “actual backend validator rejected before mutation.” Without credible evidence for the hosted route, Gate A stays blocked.

**Mandatory decision:** stop here and report the discovery/missing-arguments blocker unless both discovery and fail-closed invocation have evidence. Omitting the optional-branch tool alone is not a pass. A different ToolHive version, runtime patch, validation proxy or reduced mutation scope needs a design decision; do not silently introduce one.

## Task 3 — Nested configuration, only after Gate A and a completed policy recipe

**Future production files (not changed by this planning task):**

- Modify `kubernetes/apps/ai/toolhive/config/github.yaml`: replace `github` with private policy entry and add isolated `github-upstream` hosted entry, existing PAT reference only on upstream.
- Modify `kubernetes/apps/ai/toolhive/config/mcpgroups.yaml`: add `github-upstream` group, no raw membership in `all`.
- Create `kubernetes/apps/ai/toolhive/config/github-policy.yaml`: separate inner VMCP with anonymous incoming auth plus verified inline Cedar, priority aggregation and exact filters; no optimizer, composites or code mode.
- Modify `kubernetes/apps/ai/toolhive/config/kustomization.yaml`: register `github-policy.yaml`.
- Preserve `config/virtualmcpservers.yaml` unified optimizer and external routing; do not touch Hermes, PATs, OIDC or GitHub settings.

- [ ] Before this task becomes executable, update this blocked plan with the **complete policy and exact serialized manifests derived from passing Task 2**, and obtain parent review. This document deliberately provides no guessed Cedar policy or deployment YAML while Gate A remains open.
- [ ] Implement the approved exact topology: `all → github → http://vmcp-github-policy.ai.svc.cluster.local:4483/mcp → github-upstream group → hosted entry`. `github.spec.allowPrivateEndpoint=true`; no Authorization header or Secret reference on `github`.
- [ ] Use inner priority aggregation with `priorityOrder: [github-upstream]`, verified exact `tools[].filter` and fail-closed handling for unknown backends; outer existing prefix must yield `github_push_files`, never doubled or upstream-prefixed names.
- [ ] Ensure nonempty authz policy wiring survives operator conversion. Empty/unconfigured authz can mean allow-all; a syntactically valid YAML file is not proof Cedar is active.
- [ ] Follow existing resource/probe conventions and document additional VMCP capacity. Do not weaken requests/limits on unrelated workloads or assume readiness means all backends healthy.

## Task 4 — Full nested and denial matrix, using real v0.49.0 VMCP

**Extend the same research fixture**, not a second simulated implementation.

- [ ] Place real inner policy VMCP between real outer optimized VMCP and the counting backend. Use the actual pinned optimizer with a deterministic loopback embedding endpoint and no LiteLLM/API credentials. Keep its index/session behavior real.
- [ ] Test outer `find_tool`, outer `call_tool`, direct inner `tools/list` and inner `tools/call`. Assert exactly one outer `github_` prefix and no raw upstream names. Assert non-GitHub backend sentinel discovery/call remains unchanged.
- [ ] Implement table-driven tests with a fresh counter baseline per case:

| Inputs | Expected outcome |
|---|---|
| Owner `Tanguille` or `tanguille`, repository `cluster`, `another-repo`, future-name or fork-name, valid arbitrary branch `topic`, `release/1`, `domain`, `masterpiece` | Branch/file operation allowed; no prefix requirement or enumerated repo restriction |
| Owner `Syknapse`, `TANGUILLE`, whitespace, missing/null/number/bool/list/object; repo missing/null/wrong type/empty/invalid syntax | Denied |
| Branch `main`, `master`, `refs/heads/main`, `refs/heads/master` | Denied |
| Branch missing/null/wrong type/empty, whitespace/control characters, `a..b`, `a.lock`, trailing slash/dot, `@{`, backslash | Denied without coercion |
| Valid refs/heads alias to nonprotected branch | Consistent documented normalize-or-reject policy; never alias bypass |
| PR owned target, valid local head or verified Tanguille-qualified head, base `main`/`master`, draft omitted/false/true | Allowed |
| PR source head main/master or protected ref alias; external-qualified head; malformed/missing/null/wrong-type head/base | Denied; do not confuse `base` with file-write branch |
| Exact allowlisted read tool on public external repo | Allowed; no owner restriction applied to public reads |
| Merge, comment, review, repository create/delete/fork, unknown tool | Hidden and denied on direct invocation |
| `github-upstream.push_files`, `github-upstream_push_files`, doubled prefixes, native-name bypass, forged optimizer target names | Denied via unified; counter unchanged |
| Client-injected tool-selection/Authorization headers or conflicting policy metadata | Cannot broaden configured backend tools/policy; no credential logging |
| Inner VMCP stopped, upstream stopped, stale optimizer index after tool removal | Error, no raw fallback, no mutation counter increment |

- [ ] Exercise complete argument omission AND partial missing fields. Test null/object/list markers so `arg_branch_present` cannot accidentally satisfy a scalar requirement. Include spoofed argument keys resembling policy attributes. Do not use truthiness or automatic string conversion.
- [ ] Keep policy branch validation distinct from owner scope. Use `git check-ref-format --branch` as a local oracle for branch test data, not as an unimplemented policy validator. Test valid names without an artificial prefix restriction.
- [ ] Run `go test ./pkg/vmcp/server -run '^TestGitHubPolicy' -count=1 -race -v`. Save test names/results and deny-path counter evidence. Prove allow cases actually reach the backend once; a blanket deny is not success.

## Task 5 — Repository validation and independent review

- [ ] Render the changed ToolHive configuration with pinned tools: `mise exec -- kustomize build kubernetes/apps/ai/toolhive/config`. Inspect only a nonsecret-safe rendered scope; do not dump substituted Secret-backed environment values.
- [ ] Assert rendered resource names, groups, endpoint, `allowPrivateEndpoint`, inner incomingAuth policy, exact filters, outer prefix and absence of raw `all` membership/external route. Assert PAT reference only on `github-upstream` among these GitHub entries. Check no alternative raw fallback or composite was introduced.
- [ ] Run `bash .agents/skills/pr-review/scripts/validate-pr.sh`. Any touched shell script uses `set -euo pipefail` and passes shellcheck. If `mise`/required tools are unavailable, report blocked validation, not PASS.
- [ ] Run `git diff --check` and inspect complete intended/unrelated diff. Ask the parent for spec-coverage/security review using `pr-review` and `requesting-code-review`. Include hosted schema drift and attribution of every validation boundary.
- [ ] Acceptance requires all gates green, not just valid CRDs or tool hiding. Do not commit or propose automatic commit commands without explicit user approval.

## Task 6 — Explicitly gated rollout and safe rollback

- [ ] Obtain separate explicit permission for any commit/push and for cluster reconcile/apply. This planning request authorizes none. No real GitHub mutation tests are permitted even after deployment.
- [ ] Plan a fail-closed cutover: first remove raw `github` from unified discovery, wait for the actual backend/index/session refresh or an explicitly authorized restart to eliminate stale raw routing, then introduce the isolated raw backend/inner gateway and finally reconnect `github` through policy. Do not rely on simultaneous Flux object updates being atomic. Temporary GitHub unavailability is preferable to bypass exposure.
- [ ] After approved rollout, inspect only nonsecret live CRs, health/status and tool lists. Verify the running image is v0.49.0 and controller-generated authz/group/routing matches tested artifacts. Never print substituted environment values, Authorization headers, or Secret contents.
- [ ] If unhealthy or any gate fails, remove/disable GitHub exposure from unified while retaining raw-group isolation. **Never rollback by restoring the old raw entry in `all`.** Request approval for rollback reconciliation; do not automatically delete resources.

## Limits that must remain in the final report

- Covers all anonymous unified consumers equally, not an agent-only Hermes route.
- Covers all owned repositories including future ones/forks at the owner-argument policy layer, not a PAT repository-permission guarantee.
- Exact normalized `main`/`master` are protected; other default branch names are not.
- Normal/draft PRs may target main/master; source head restrictions remain separate.
- Direct API/git/gh/SSH credentials and clients outside unified remain outside scope; no cluster-wide network isolation claim.
- Public reads remain available by exact tools; private-read capability follows the existing token and is not changed by this design.
- Hosted tools/schemas can change independently of ToolHive's pin. Tool exposure is not token permission.

## Planning verification actually performed

- Read the full approved spec and current operator pin.
- Repeated root Git preflight; pull reported already up to date and unrelated untracked paths remained.
- Read official pinned ToolHive source via raw GitHub and read-only `gh api --method GET` source/CRD projections; consulted Context7 and official hosted MCP documentation for header selection.
- Verified authz placement, scalar argument mapping, discovery/empty-call ambiguity, core forwarding behavior, private entry fields and `/mcp` default; identified unproven header exclusivity and nesting.
- Did **not** run mock integration, Go tests, operator conversion, cluster checks, or manifest validation for a change. No manifests exist to validate in this task.
- Self-review: scope, PR base/head distinction, optional branch-tool omission, arbitrary valid branch requirement, zero real GitHub writes, anonymous shared policy and fail-closed rollback are covered above. Full implementation remains blocked at Gate A; parent owns final spec coverage.

## Source references

- https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/vmcp/core/admission.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/vmcp/core/core_calls.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/authz/authorizers/cedar/core.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/authz/authorizers/cedar/entity.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/vmcp/server/server.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/cmd/thv-operator/api/v1beta1/virtualmcpserver_types.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/cmd/thv-operator/api/v1beta1/mcpserver_types.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/cmd/thv-operator/api/v1beta1/mcpserverentry_types.go
- https://github.com/stacklok/toolhive/blob/v0.49.0/deploy/charts/operator-crds/files/crds/toolhive.stacklok.dev_virtualmcpservers.yaml
- https://github.com/stacklok/toolhive/blob/v0.49.0/examples/operator/mcp-server-entries/mcpserverentry_with_header_forward.yaml
- https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md (hosted documentation, not a pinned implementation guarantee)
