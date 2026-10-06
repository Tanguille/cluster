# ToolHive GitHub Policy — Staged Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `executing-plans` or `subagent-driven-development`, and `verification-planning`. Use checkbox steps. Stage 1 is local validation only; stop before production edits until its evidence passes parent review. No automatic commits, pushes, reconciles, GitHub writes or Secret access.

**Goal:** Constrain potentially successful unified GitHub mutations to all Tanguille-owned repositories and valid branches other than `main`/`master`, while preserving explicit public-read tools and owned-target normal/draft PR creation.

**Architecture:** Keep the existing anonymous unified optimizer. Its `github` entry points to a separate non-optimized Cedar `github-policy` VMCP, which alone consumes the isolated hosted `github-upstream` group. A narrow no-owner discovery exception depends on GitHub's actual required-owner handler checks; it does not promise zero malformed forwarding.

**Tech Stack:** ToolHive operator/CRDs/VMCP v0.49.0, actual Cedar authorizer, Flux/Kustomize, hosted GitHub MCP, Go tests with local-only mock backends and actual runtime components.

---

## Accepted boundary, evidence and execution scope

The user explicitly accepted backend validation. **Gate A's semantic decision is closed.** It is not a request for a new validator service, ToolHive upgrade, agent-only endpoint, PAT changes or OIDC. Nested runtime and complete policy validation remain mandatory, separate gates.

The only no-owner exception is for exact native `create_branch`, `push_files`, and `create_pull_request`, with BOTH `arg_owner` and `arg_owner_present` absent and all other security arguments/markers absent (`repo`, `branch`, `head`, `base`, `reviewers`). It may forward an invocation, but the invocation cannot mutate because upstream requires owner. Supplied scope must pass the full tool-specific policy; null, wrong-type and partial scope do not get this exception. Omit `create_or_update_file`. Reject PR `reviewers` presence, even an empty array or null. Do not force `maintainer_can_modify` false or alter its default.

Verified source/evidence:

- ToolHive v0.49.0 is `e532cf07d45fa99f3e4e63819396a3e9c9fd763f`; `main` also resolved to this SHA during Gate A inspection.
- `pkg/vmcp/core/admission.go:145–187`: FilterTools and AllowToolCall use the same operation/name/annotations, with nil arguments for discovery.
- `pkg/authz/authorizers/cedar/core.go:1021–1041,1068–1101,1272–1340`: scalars map to `arg_*`, nonscalars/null to `arg_*_present`; arguments reach both resource and context. No trusted discovery flag exists in the inspected path.
- Real authorizer **20 leaf cases PASS**, package runtime 0.012s, in `/tmp/opencode/toolhive-github-policy-gatea-20260913/pkg/authz/authorizers/cedar/github_policy_gatea_test.go`. This tests one repository/branch tuple, not the full production policy.
- The larger `pkg/vmcp/core/github_policy_gatea_test.go` experiment **did not execute**: compilation failed with `no space left on device` on the 3.9 GiB `/tmp` tmpfs. No HTTP or backend counter evidence exists yet.
- GitHub MCP source `7d13a7ad6f2a17f351a6d77ce280c85ae1821f4d`: `repositories.go` CreateBranch (1499+) and PushFiles (1612+) require owner/repo/branch before client/API use; `pullrequests.go` CreatePullRequest (650+) requires owner/repo before interactive form or mutation; `params.go:128–147` RequiredParam rejects missing, null/wrong-type and zero strings without coercion/defaults. This is explicit handler code, not merely JSON Schema.required.
- **Hosted revision uncertainty remains:** source inspection does not attest hosted GitHub's deployed commit. The user accepts the required-owner contract dependency; record drift and stop if it ceases to hold. A handwritten validating mock cannot attest hosted behavior.

Current authorization is documentation only. No cluster-repository pull/switch or production writes while the concurrent user lane is active. Future temporary writes require their own allowed research scope. Reuse the existing research path if authorized; do not create a new worktree/disk-backed location implicitly. No live cluster/Secret access or real GitHub mutation tests in Stage 1.

## File map

**Existing local evidence:** `/tmp/opencode/toolhive-github-policy-gatea-20260913/GATE-A-RESULT.md` and the two tests above.

**Stage 1 future research files, only in that authorized research checkout:**

- `pkg/authz/authorizers/cedar/github_policy_gatea_test.go`: extend argument/marker regression matrix using real authorizer.
- `pkg/vmcp/core/github_policy_gatea_test.go`: add backend-counter control using existing core fixtures.
- `pkg/vmcp/server/github_policy_nested_test.go`: two real VMCP HTTP servers and actual outer optimizer.
- `pkg/vmcp/server/testdata/github-policy-tools.json`: nonsecret exact tool schema capture.
- `pkg/vmcp/server/testdata/github-policy.cedar`: candidate full policy, shared by authorizer and nested tests.

**Stage 2 production changes, NOT authorized until Stage 1 review:**

- Modify `kubernetes/apps/ai/toolhive/config/github.yaml`: private `github` entry plus isolated `github-upstream` hosted entry.
- Modify `kubernetes/apps/ai/toolhive/config/mcpgroups.yaml`: add `github-upstream` group.
- Create `kubernetes/apps/ai/toolhive/config/github-policy.yaml`: inner VMCP with tested Cedar/filter configuration.
- Modify `kubernetes/apps/ai/toolhive/config/kustomization.yaml`: register that file.
- Preserve unified `virtualmcpservers.yaml`, HTTPRoute, Hermes, Secrets and version pins unless a reviewed necessary change is explicitly approved.

## Stage 1 / Task 1 — Reuse evidence and check compilation capacity

- [ ] Read approved spec and nearest repo guidance. Do not repeat Git pull/switch in the concurrent root worktree. Read-only status is sufficient if needed; no commits or pushes.
- [ ] From the existing research checkout run `git rev-parse HEAD` and `df -h .`; expect the ToolHive SHA above. Do not attempt another full build on an exhausted filesystem. Lower parallelism reduces peaks but is not proof sufficient space exists.
- [ ] Reproduce the small passing test, from that directory:

```bash
export PATH="$PWD/go/bin:$PATH"
export GOPATH="$PWD/.gatea-gopath" GOCACHE="$PWD/.gatea-gocache" GOTMPDIR="$PWD"
go test -p 2 -vet=off ./pkg/authz/authorizers/cedar \
  -run '^TestGitHubPolicyGateA$' -count=1 -v
```

Expected: 20 existing leaf cases pass; strict discovery/empty denied, bounded exception discovery/empty allowed, tested supplied-scope violations denied. This is not backend dispatch proof. `-vet=off` is only for bounded research; standard repository validation still applies later.

- [ ] Set a total ten-minute compilation budget for the next runtime build. If capacity is insufficient or compilation fails again, stop with the actual error and request an explicitly authorized disk-backed research location with adequate free space. Do not allocate in `.worktrees`, use Docker storage elsewhere, or delete unrelated caches without permission.

## Stage 1 / Task 2 — Encode the accepted exception and complete the policy locally

The following is **concrete candidate fixture code**, adapted from the passing experiment. The expanded three-tool form/markers have NOT yet been executed. It is an exception fragment, not a complete production policy:

```cedar
permit(principal, action == Action::"call_tool", resource)
when {
  [Tool::"create_branch", Tool::"push_files", Tool::"create_pull_request"].contains(resource) &&
  !(resource has arg_owner) && !(resource has arg_owner_present) &&
  !(resource has arg_repo) && !(resource has arg_repo_present) &&
  !(resource has arg_branch) && !(resource has arg_branch_present) &&
  !(resource has arg_head) && !(resource has arg_head_present) &&
  !(resource has arg_base) && !(resource has arg_base_present) &&
  !(resource has arg_reviewers) && !(resource has arg_reviewers_present)
};

forbid(principal, action == Action::"call_tool", resource == Tool::"create_pull_request")
when { resource has arg_reviewers || resource has arg_reviewers_present };
```

Use this **already-tested narrow strict form** as a fixture control (the original test has the same owner/repo/branch predicates). Never deploy its one-repository/one-branch restriction:

```cedar
permit(principal, action == Action::"call_tool", resource == Tool::"push_files")
when {
  resource has arg_owner && resource.arg_owner == "Tanguille" &&
  resource has arg_repo && resource.arg_repo == "cluster" &&
  resource has arg_branch && resource.arg_branch == "topic"
};
```

- [ ] Compile the expanded exception using the existing `NewCedarAuthorizer` test setup, anonymous identity and actual `AuthorizeWithJWTClaims`. Test all three tool names plus unknown/merge names. Record that accepted no-owner calls remain non-mutating by the inspected required-owner contract, not by pretending Cedar rejected them.
- [ ] Add explicit regression cases for each security key: absent, null, bool, number, object, array, empty string, and partial supplied fields. A scalar `owner_present` argument must not reopen discovery; test marker-like spoofed keys. PR reviewers omitted is allowed by this guard, while null/empty/nonempty forms are all forbidden.
- [ ] Capture exact hosted tool schemas using an already-authorized read-only tools/list route if separately permitted, without extracting credentials. Otherwise use the existing nonsecret schema evidence and retain hosted-schema confirmation as a gate. No live mutation calls, including deliberately invalid ones.
- [ ] Complete strict predicates for all Tanguille/tanguille repositories, not an enumerated repo list: valid scalar repo, valid arbitrary explicit branch except protected names/aliases, and valid local or Tanguille-qualified PR head with valid base. No branch prefix requirement. Reject or normalize refs/heads aliases consistently. Read tools must use an explicit native-name allowlist, not annotation-only authorization or wildcards.
- [ ] Validate full branch/repo/head predicates with actual Cedar before attempting deployment. Use `git check-ref-format --branch` as a local test-data oracle, not an imaginary Cedar function. Do not silently narrow allowed valid branches to a regex convenience subset or add an external validator service. If Cedar cannot express the approved validation, report that specific gap for review.
- [ ] Store the resulting complete candidate in `testdata/github-policy.cedar`; run the same policy bytes in the authorizer and nested tests. Separate strict tool-specific permits and no-owner exception; do not add a broad permit that negates missing-field checks.

Expected evidence: actual policy parse/evaluation results, exact read list, owned arbitrary-branch positive cases and malformed/ref-alias negatives. The 20 historical cases are reusable baseline evidence, not acceptance for these expanded predicates.

## Stage 1 / Task 3 — Real core counter, then the real nested HTTP path

Reuse existing pinned scaffolding, not a reimplemented fake authorization model:

- `pkg/vmcp/core/admission_test.go:528–575`, `TestAdmission_ListCallLookupEnforceSameDecision`: `baseConfig`, `cedarAuthzConfig`, actual `New`, `ListTools`, `CallTool`, mocked backend client.
- `pkg/vmcp/server/authz_integration_test.go`: actual server/authentication/HTTP setup.
- `pkg/vmcp/server/server.go`, `New`: real authz composition; do not use direct `Serve` as a replacement. Authz and optimizer are mutually exclusive on one instance, hence the two instances.

- [ ] Extend the core fixture with a permissive backend counter. The actual runtime must decide authorization. Use a callback on the existing mock's `CallTool` expectation:

```go
var dispatches atomic.Int64
m.client.EXPECT().CallTool(
    gomock.Any(), gomock.Any(), gomock.Any(),
    gomock.Any(), gomock.Any(), gomock.Any(),
).DoAndReturn(func(
    _ context.Context, _ *vmcp.BackendTarget, _ string,
    _ map[string]any, _ map[string]any, _ map[string]string,
) (*vmcp.ToolCallResult, error) {
    dispatches.Add(1)
    return &vmcp.ToolCallResult{StructuredContent: map[string]any{"marker": "local-only"}}, nil
}).AnyTimes()
```

The callback signature matches `pkg/vmcp/mocks/mock_backend_client.go:85`. Use the upstream helper's registry/aggregation configuration; do not replace admission with a conditional in the mock. Import `sync/atomic` and the symbols already used by the existing test.

- [ ] Sensitivity controls: authz absent permits dispatch; deny-all authz blocks it; strict fixture permits the positive tuple; unknown tool has no route. Test no-owner exception **expects** dispatch to the permissive backend, whereas supplied invalid owner/branch **expects zero** dispatch. Do not count a harmless marker as a GitHub mutation or call a permissive mock proof of backend rejection.
- [ ] For a separate backend-contract regression, run the actual inspected GitHub handlers with inert dependencies that fail the test on `GetClient` or any API access. Nil/empty/unrelated-only maps must return missing-owner errors before dependencies are used. Reuse its real `RequiredParam`/handler code, not a copied handwritten approximation. Source hash above is the required baseline; no credential or real GitHub URL may be present.
- [ ] Build real inner policy VMCP over loopback MCP backend and real outer VMCP over the inner `/mcp` URL. The outer must use the actual v0.49.0 optimizer with a deterministic local embedding endpoint. Start from upstream server tests; do not build the operator or deploy anything to a cluster for this gate.
- [ ] All listeners and outbound fixture transports must be loopback-only; reject other targets before execution. No GitHub client/credentials in the permissive mock. Distinguish dispatch count from actual-handler API-attempt count; expected accepted malformed request has dispatch > 0, mutation/API attempts = 0 in the contract fixture.
- [ ] Run from the research checkout with the Task 1 environment:

```bash
go test -p 2 -vet=off ./pkg/vmcp/core \
  -run '^TestGitHubPolicyGateA$' -count=1 -v
go test -p 2 -vet=off ./pkg/vmcp/server \
  -run '^TestGitHubPolicyNested' -count=1 -v
```

The nested tests are to be created in this task; these commands are acceptance commands, not claims they already exist or pass. No full optimizer work before the core gate and build capacity check pass.

## Stage 1 / Task 4 — Nested acceptance matrix and conversion evidence

- [ ] Exercise direct inner tools/list and tools/call, outer find_tool and call_tool, then assert this matrix against real authorization and route counters:

| Case | Required evidence |
|---|---|
| All security args/markers absent | Discoverable; invocation may dispatch; actual required-owner contract prevents mutation |
| Owner absent but repo/head/base/branch supplied; any malformed scope; owner present with branch missing | Cedar denies, no dispatch; PRs do not require branch but do require valid head/base |
| `Tanguille`/`tanguille`, cluster/another/future/fork repo, `topic`, `release/1`, `domain`, `masterpiece` | Positive calls reach permitted backend; no invented prefix or fixed repo restriction |
| `Syknapse`, `TANGUILLE`, whitespace/invalid owner or repo | Denied before dispatch |
| main/master and refs/heads/main or master | Branch/file writes and protected PR source heads denied |
| Invalid branch: empty, whitespace/control, a..b, a.lock, trailing slash/dot, @{, backslash | Denied with supplied scope; no coercion |
| Owned PR target, valid local/owned-qualified head, base main/master, draft omitted/false/true | Allowed; normal PR remains valid; do not force maintainer_can_modify false |
| External-qualified/malformed head/base; reviewers present (null/empty included) | Denied before dispatch |
| Exact approved read on public external repo | Allowed; existing private-read token capability unchanged |
| merge, comment, review, fork, create/delete repo, create_or_update_file, unknown tools | Hidden and direct invocation denied |
| Raw/dotted/upstream/doubled names, malicious optimizer target | No bypass; outer names have exactly one github_ prefix |
| Inner/upstream stopped, stale index/session after removal | Error, no raw fallback; non-GitHub sentinel still behaves as configured |
| Client header or argument marker spoofing | Cannot broaden exception, filters or policy |

- [ ] Verify pinned CRD/converter fields once, without broad rediscovery: `spec.incomingAuth.authzConfig`, inline type/policies/entitiesJson, `config.aggregation.tools[].workload/filter`, priorityOrder and private endpoint flag. Render or locally test operator conversion to ensure active nonempty authz reaches runtime; no cluster apply.
- [ ] Establish exact X-MCP-Tools default interaction. Verified map shape is `headerForward.addPlaintextHeaders`; docs say tools are enabled, not necessarily that defaults are replaced. An empty X-MCP-Toolsets may select defaults. Require exact resulting tools/list before labeling the header an exclusive allowlist; inner filters and Cedar remain enforcement layers.

**Hard stop before production edits:** parent reviews full policy, nested integration, actual required-owner regression/source dependency, exact tools/header behavior and name/Service conversion evidence. Missing evidence is a named remaining gate, not a reopened rejection of the user-accepted backend boundary.

## Stage 2 — Known configuration shapes, then reviewed production edits

The following replacement entry is source-supported exact configuration. Its endpoint/name still needs the Stage 1 operator-Service and nested-path checks. It has no PAT:

```yaml
apiVersion: toolhive.stacklok.dev/v1beta1
kind: MCPServerEntry
metadata:
  name: github
spec:
  remoteUrl: http://vmcp-github-policy.ai.svc.cluster.local:4483/mcp
  allowPrivateEndpoint: true
  transport: streamable-http
  groupRef:
    name: all
```

The dedicated group:

```yaml
apiVersion: toolhive.stacklok.dev/v1beta1
kind: MCPGroup
metadata:
  name: github-upstream
spec:
  description: GitHub upstream consumed only by the policy gateway
```

Source-supported inner authorization fragment for the **deny-all sensitivity control only**, not final policy. `mcpserver_types.go` defines `AuthzConfigTypeInline = "inline"`; this belongs under the inner VMCP `spec`:

```yaml
incomingAuth:
  type: anonymous
  authzConfig:
    type: inline
    inline:
      policies:
        - 'forbid(principal, action, resource);'
      entitiesJson: '[]'
```

For the allowed-case fixture, substitute the exact tested policy strings. For production, use only the complete Stage 1 reviewed policy; do not remove authz to restore discovery.

Source-supported inner aggregation fragment (mutation-only fixture list, NOT the final read-inclusive manifest):

```yaml
config:
  aggregation:
    conflictResolution: priority
    conflictResolutionConfig:
      priorityOrder: [github-upstream]
    tools:
      - workload: github-upstream
        filter: [create_branch, push_files, create_pull_request]
```

Known header **shape only**, not a verified exclusive selection recipe:

```yaml
headerForward:
  addPlaintextHeaders:
    X-MCP-Tools: create_branch,push_files,create_pull_request
```

- [ ] After Stage 1 approval, serialize the tested full policy/read list into `github-policy.yaml` at `spec.incomingAuth.authzConfig`, with anonymous incoming auth and no optimizer/composites/code mode. Do not deploy the narrow fixture policy or mutation-only lists above as final configuration.
- [ ] Move existing hosted entry to `github-upstream` and group `github-upstream`; retain existing Secret reference `toolhive-secrets/GITHUB_PERSONAL_ACCESS_TOKEN` only there. Never read/change its value. Add exact approved read tools to upstream header selection and inner filter from one reviewed list; preserve native names at inner priority layer and existing outer prefix.
- [ ] Register manifest in Kustomization. Follow existing resource/probe conventions; budget extra inner VMCP capacity without changing unrelated workloads. Do not claim readiness proves all backends healthy.
- [ ] Before writing production files, the executing agent must present the fully serialized policy/config derived from passing fixtures for parent review. This is a staged plan, not a claim that untested full branch validation or the final read list already exists.

## Stage 3 — Repository validation and independent review

- [ ] Run `mise exec -- kustomize build kubernetes/apps/ai/toolhive/config` on the nonsecret-safe scope; avoid substituted Secret values. Assert resource names/group memberships, exact /mcp endpoint, allowPrivateEndpoint, nonempty inner authz, filters, one prefix, PAT reference only upstream and no alternate raw route/composite.
- [ ] Run `bash .agents/skills/pr-review/scripts/validate-pr.sh`. Touched shell scripts require `set -euo pipefail` and shellcheck. Missing tooling is a validation failure, not PASS.
- [ ] Run `git diff --check` and review complete intended diff with `pr-review`/`requesting-code-review`. Parent owns final spec coverage; implementer owns source/runtime/render evidence. Rerun integration when policy, runtime, tool schemas, aggregation or header configuration changes.

## Stage 4 — Explicit rollout and safe rollback gates

- [ ] Obtain separate permission for commit/push and cluster reconciliation. No real GitHub mutation tests even after deployment. Approval of this plan is not rollout permission.
- [ ] Plan fail-closed cutover: remove raw GitHub from unified, confirm stale sessions/index/raw routes are gone (an authorized restart may be necessary), introduce isolated upstream/inner gateway, then reconnect the `github` policy entry. Flux multi-object updates are not atomic; temporary GitHub unavailability is acceptable.
- [ ] After authorized rollout, inspect nonsecret live image/CR/routing/health and tool lists. Compare with tested v0.49.0 artifacts without dumping substituted environments, headers or Secrets.
- [ ] On failure, disable GitHub exposure from unified while retaining upstream isolation. Never restore the old raw entry in `all`. Obtain permission for rollback reconciliation; no automatic deletion/revert that reopens raw access.

## Limits and self-review

- All anonymous unified clients share restrictions; no agent-only Hermes wiring or cluster-wide network-isolation claim.
- All owned repositories, including forks/future repos; only exact normalized main/master protected, not other default branch names. No branch prefix requirement.
- Normal/draft PRs may target main/master; source and target checks stay distinct; reviewers presence rejected, maintainer_can_modify default unchanged.
- Out-of-path gh/git/SSH/API credentials and token/ruleset changes remain out of scope. Tool exposure does not prove PAT permissions; private reads remain governed by the unchanged token.
- Historical 20-case pass proves the small authorizer ambiguity only. Full predicates, nested runtime, exact tool/header exposure and deployment remain unverified.
- Source hash required-owner dependency is explicit; the hosted deployed revision is uncertain and accepted as a dependency, not silently attested by mocks.
- Self-review confirms no zero-malformed-forwarding claim, no automatic Git operations, and a hard Stage 1 stop before production changes. Parent reviews final coverage and execution choice; this documentation update does not begin implementation.
