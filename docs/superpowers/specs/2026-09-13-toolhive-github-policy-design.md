# ToolHive GitHub Policy Gateway

**Status:** Approved design; backend-validation boundary accepted, integration pending; **Date:** 2026-09-13; **Owner:** Tanguille

## Summary

Constrain every GitHub tool exposed through the unified ToolHive endpoint with a
dedicated Cedar policy gateway. Public reads remain available through an exact
native-tool allowlist. Writes are limited to Tanguille-owned repositories and
non-`main`/`master` branches. Pull requests may target `main` or `master`, but
only when the target repository is owned by Tanguille. Merge, review/comment,
repository lifecycle, fork, and unrelated mutation capabilities remain denied.

This policy applies to all unified consumers anonymously. It covers all
Tanguille-owned repositories, including forks and future repositories. Direct
paths and credentials outside the unified gateway are not covered.

## Scope and non-goals
Public reads and discovery remain available through an explicit allowlist;
potentially successful mutations are constrained by Cedar plus the actual GitHub
handlers' required-owner validation. Discovery-equivalent malformed calls may
be forwarded, but must fail before any mutation. There is no guarantee of zero
malformed-request forwarding. Only exact
`main` and `master` are protected; other configured default branches are out of
scope.

Out of scope: PAT/ruleset changes, Hermes configuration, PR #4757, comments,
reviews, fork/create-repository/delete tools, and composite draft tooling. Draft
is optional; normal PR creation is allowed.

## Approved topology
The pinned ToolHive version is v0.49.0. Because one VMCP cannot combine Cedar
and the optimizer, isolate the raw hosted GitHub entry and place policy in a
dedicated nested gateway:

```text
Hermes/unified consumers
          |
          v
 unified VMCP (optimizer; existing external endpoint)
          |
          v
 github (replacement MCPServerEntry)
          |
          v
 github-policy VMCP (Cedar; explicit aggregation filters)
          |
          v
 github-upstream (raw hosted GitHub entry; PAT injection only here)
```

The replacement `MCPServerEntry` named `github` points to:

```text
http://vmcp-github-policy.ai.svc.cluster.local:4483/mcp
```

It sets `allowPrivateEndpoint: true` and injects **no PAT**. The isolated
`github-upstream` entry alone injects the existing PAT and exposes the exact
native GitHub tool allowlist. There must be no raw upstream entry in `all`, and
no alternate externally exposed raw endpoint.
The raw entry belongs only to a dedicated `github-upstream` group consumed by
`github-policy`. Inner priority aggregation preserves native tool names; the
outer `github` entry supplies the existing `github_` prefix exactly once.

Future implementation locations: `config/github.yaml`, `mcpgroups.yaml`, and
`virtualmcpservers.yaml` (or a separate manifest plus kustomization), following
repository conventions. Unified remains optimized.

## Tool exposure
The upstream allowlist is explicit and limited to `create_branch`, `push_files`,
`create_pull_request`, and exact read/discovery entries selected from hosted
`tools/list` (no wildcard).

Tool discovery without arguments remains visible through the bounded exception
below. It does not authorize a successful mutation without the required scope
arguments. Do not promise
content-path restrictions: file presence is not a practical scope control.

Omit `create_or_update_file` entirely in this implementation. Its optional branch
default is unnecessary additional scope.

### Accepted discovery and backend-validation boundary

ToolHive v0.49.0 `FilterTools` and `AllowToolCall` use the same Cedar action,
resource and annotations; discovery supplies nil arguments. Scalar arguments
become `arg_<name>` in both resource and context; nonscalars become
`arg_<name>_present`. Neither location supplies a trusted discovery discriminator.

Permit a discovery exception only for the three exact permitted mutation names
when **both `arg_owner` and `arg_owner_present` are absent**, and all other
security arguments (`repo`, `branch`, `head`, `base`, `reviewers`) and their
`_present` markers are absent. Such a call may dispatch upstream, but necessarily
lacks `owner` and must fail the actual handler's required-owner check before any
mutation. Reject PR `reviewers` whenever present, including null or an empty list.

Once any scope argument is supplied, the exception does not apply: the full
tool-specific owner/repository/branch or PR policy must permit the call. Null,
wrong-type and partially supplied scope fields must not reopen the exception.
No `branch` argument is required for PR creation. Normal/draft PRs remain allowed;
this design does not force `maintainer_can_modify` false or change its default.

The user explicitly accepted this backend-validation dependency. Inspected
GitHub MCP source commit `7d13a7ad6f2a17f351a6d77ce280c85ae1821f4d` checks
`RequiredParam[string](owner)` and `repo` in all three handlers before mutation;
branch/file handlers also require `branch`. `RequiredParam` rejects absent,
null/wrong-type and empty-string values, with no owner default or coercion.
Hosted GitHub's deployed revision is not attested by that source inspection.
The guarantee depends on preserving this required-owner contract; source/schema
drift must be reviewed. Public/private read permissions of the existing token
are unchanged. Tool exposure is not token permission.

## Policy semantics
These are outcome requirements, not guessed Cedar syntax. Verify v0.49.0
attribute names and request context from the implementation and hosted
`tools/list` before encoding policy or tests.

### Repository owner
For mutations, `owner` must be a scalar string exactly equal to `Tanguille` or
`tanguille`; `repo` must also be a valid scalar repository name.
Any other, null, or wrong-type owner/repo is denied by Cedar. Missing scope is
denied except for the bounded no-owner discovery exception, which fails at the
backend before mutation. Do not rely on a
live metadata lookup for this decision.

### Branches
Branch-creation and file-write arguments must contain a valid, explicit string
`branch`. Missing,
null, wrong-type, or malformed `branch` is denied when scope arguments are
supplied; the no-owner discovery exception cannot produce a mutation. Normalize or reject aliases
such as `refs/heads/main` and `refs/heads/master` consistently before the
protected-branch decision. Exact normalized `main` and `master` are denied.
Names containing those words but not equal to them may be allowed when valid.

The policy intentionally does not protect other repository default branches.

### Pull requests
`create_pull_request` requires an owned target repository. Its `base` may be
`main` or `master`; the protected-branch write rule applies to the source/head
branch, not the PR target. The head must be an explicit valid local branch, or a
Tanguille-qualified head accepted by the verified hosted schema. Reject external
head ownership or any head that could escalate repository scope.

Missing, null, wrong-type, or malformed owner/repo/head/base values are denied
rather than coerced, except for the non-mutating no-owner discovery exception.
PR `reviewers` presence is denied. PR creation does not require a `branch` argument. A PR may
be draft or normal.

### Tool and failure boundaries
Unknown tools, merge operations, review/comment operations, fork/repository
lifecycle operations, and unrelated mutations are denied. A call that attempts
to reach the raw upstream entry, bypass aggregation, or use optimizer-known
malicious tool names is denied. If the inner gateway or upstream is unavailable,
the outer gateway fails closed rather than exposing a fallback.

## Validation and rollout gates
1. Confirm pinned v0.49.0 CRDs, VMCP schema, hosted `tools/list`, argument
   schemas, and request-context attributes for every permitted tool. Implement
   Cedar only after this; do not infer syntax or attributes.
2. Gate the extra nested hop with a mocked-backend integration test; there is no
   upstream integration-test evidence for this topology yet.
3. Test owned feature-branch operations; external target (`Syknapse`); missing,
   null, wrong-type, and malformed arguments; `main`, `master`, and
   `refs/heads/*`; hidden raw/optimizer bypasses; discovery versus invocation;
   and inner gateway/backend failure.
4. Compare rendered manifests and routing; verify no raw endpoint is
      externally exposed and no PAT is present on the policy entry.
5. Run `bash .agents/skills/pr-review/scripts/validate-pr.sh` before completion.
6. Obtain explicit rollout permission. Do not decrypt or edit secrets in this
   design-only phase.

No real GitHub write tests are permitted. Rollback retains isolation and must
never re-enable raw `all` exposure.

## Verified evidence and remaining gates
- ToolHive v0.49.0 server source: https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/vmcp/server/server.go
- ToolHive v0.49.0 repository: https://github.com/stacklok/toolhive/tree/v0.49.0

The actual ToolHive v0.49.0 Cedar authorizer passed 20 leaf cases in
`/tmp/opencode/toolhive-github-policy-gatea-20260913/pkg/authz/authorizers/cedar/github_policy_gatea_test.go`.
Strict checks denied discovery/empty invocations; adding a guarded discovery
exception allowed both while denying tested partial/null/object/external-owner
and main cases. This small one-repository/one-branch experiment is not the full
policy and proves neither arbitrary branch validation nor nested integration.
The larger VMCP core test failed to build with `no space left on device` on the
3.9 GiB `/tmp` tmpfs. No backend-counter or HTTP integration test completed.

Gate A's semantic trade-off is accepted, not an unresolved absolute blocker.
Before production edits, complete the staged real nested-runtime integration,
full policy matrix, exact hosted-tool selection/header semantics and rendering
checks in the plan. These remain hard gates; the authorizer pass is not rollout
approval. No new disk-backed location or worktree is implicitly authorized.

Handler references at the inspected GitHub MCP commit: `pkg/github/repositories.go`
(`CreateBranch`, `PushFiles`), `pkg/github/pullrequests.go` (`CreatePullRequest`),
and `pkg/github/params.go:128–147` (`RequiredParam`).

## Self-review
Self-review: PR target (`base`) is distinct from mutation source (`branch/head`);
draft is optional; no-owner discovery calls rely on accepted backend validation;
supplied-scope malformed values, reviewers and ref aliases are covered; ownership
is limited to Tanguille. Full policy and nested-runtime evidence remain pending.
