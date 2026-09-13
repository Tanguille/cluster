# ToolHive GitHub Policy Gateway

**Status:** Design proposal; **Date:** 2026-09-13; **Owner:** Tanguille

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
permitted writes fail closed on unavailable or malformed arguments. Only exact
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
`create_or_update_file` (only if explicit branch enforcement is possible),
`create_pull_request`, and exact read/discovery entries selected from hosted
`tools/list` (no wildcard).

Tool discovery without arguments remains visible, but does not grant invocation
with missing, null, wrong-type, or malformed arguments. Do not promise
content-path restrictions: file presence is not a practical scope control.

`create_or_update_file` has a dangerous optional branch default. Prefer omitting
it unless the policy can require an explicit valid branch at execution time. It
may be enabled only after the actual hosted schema and Cedar context have been
verified and mocked integration tests prove the requirement.

## Policy semantics
These are outcome requirements, not guessed Cedar syntax. Verify v0.49.0
attribute names and request context from the implementation and hosted
`tools/list` before encoding policy or tests.

### Repository owner
For mutations, `owner` must be a scalar string exactly equal to `Tanguille` or
`tanguille`; `repo` must also be a valid scalar repository name.
Any other, missing, null, or wrong-type owner/repo is denied. Do not rely on a
live metadata lookup for this decision.

### Branches
Branch-creation and file-write arguments must contain a valid, explicit string
`branch`. Missing,
null, wrong-type, or malformed `branch` is denied. Normalize or reject aliases
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
rather than coerced. PR creation does not require a `branch` argument. A PR may
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

## References and factual blockers
- ToolHive v0.49.0 server source: https://github.com/stacklok/toolhive/blob/v0.49.0/pkg/vmcp/server/server.go
- ToolHive v0.49.0 repository: https://github.com/stacklok/toolhive/tree/v0.49.0

The links are reference points, not evidence that a particular Cedar attribute
or aggregation field exists. Verify source, CRDs, hosted schemas, and request
context before implementation. The design is not implementation-ready until
those checks and the mocked nested-gateway gate pass.

## Self-review
Self-review: PR target (`base`) is distinct from mutation source (`branch/head`);
draft is optional; missing arguments and ref aliases are covered; ownership is
limited to Tanguille. Factual blockers remain explicit rather than guessed.
