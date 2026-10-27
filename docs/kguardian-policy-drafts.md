# kguardian network policy draft reference

Phase 2 completed locally on 2026-10-28 after the owner authorized all eight
triaged namespaces in parallel. There is one Conventional Commit per namespace
across four isolated worktrees. No push, PR, apply, reconciliation, audit
deployment or enforcement occurred. Security contexts and kguardian settings
remain unchanged. These are real policy manifests: deploying the branches
directly would enforce them and is not the next step.

## Review locations

All worktrees are under `.claude/worktrees/`; branch names start with `harden/`.
Each contains `docs/kguardian-<namespace>-policy-draft.md` and the namespace's
`kubernetes/apps/<namespace>/networkpolicy.yaml`.

| Namespace | Worktree suffix | Branch suffix | Local commit |
|---|---|---|---|
| default | kguardian-default-observability | kguardian-default-observability | ac0ba5567 |
| observability | kguardian-default-observability | kguardian-default-observability | 22aa51a8f |
| web3 | kguardian-media-web3 | kguardian-media-web3 | 8a6abfde3 |
| media | kguardian-media-web3 | kguardian-media-web3 | b25c0ecbd |
| ai | kguardian-ai | kguardian-ai | b790c35f0 |
| flux-system | kguardian-controllers | kguardian-controllers | 36715f242 |
| renovate | kguardian-controllers | kguardian-controllers | 12ce4db81 |
| kritika | kguardian-controllers | kguardian-controllers | Same commit as this reference |

The common base was `fdd3cd954`. During drafting, origin/main advanced to
`0e61ea6d5` with bootstrap/CI changes and detached Paseo manifests. Paseo is not
referenced by the AI namespace and has no live workloads in the snapshot, so it
does not change this draft's active graph. Future activation needs its own
policy and capture. Rebase and revalidate against current main before publishing;
the reported checks belong to these local branches and their stated base.

## Evidence coverage

Read-only inspection found 22 CNPs, zero CCNPs and 14 native NetworkPolicies.
Traffic was queried first, then policy generation attempted for every one of
137 present pods across these namespaces, including retained Jobs. There were
122 generated policies and 15 no-data errors. Pod-specific historical policy
results and namespace traffic results differ substantially:

| Namespace | Pods attempted / generated / no data | Traffic records / pods / drops |
|---|---|---|
| default | 20 / 19 / 1 | 1 / 1 / 0 |
| observability | 44 / 32 / 12 | 0 / 0 / 0 |
| media | 20 / 20 / 0 | 4957 / 2 / 176 |
| web3 | 4 / 4 / 0 | 5 / 1 / 1 |
| ai | 36 / 34 / 2 | 0 / 0 / 0 |
| flux-system | 6 / 6 / 0 | 0 / 0 / 0 |
| renovate | 5 / 5 / 0 | 24 / 1 / 0 |
| kritika | 2 / 2 / 0 | 9 / 4 / 0 |

Zeros are not safety evidence. Drops shown here predate these unpublished
drafts. Generated policies contain retained/historical peers; selectors were
generalized using current workload labels, Service target ports and configured
dependencies, then checked against CronJobs, Jobs, movers and scaled-zero apps.
Raw captures, credentials, addresses and personal identifiers are not committed.

The write-rate branch was still unmerged. At inspection, evaluator/compute
were enabled and capture excluded only kguardian and kube-system, not the
larger exclusion set proposed on that branch. `llmBridge` remains enabled.
Cilium L7 and transparent DNS proxy settings were enabled. These facts must be
refreshed before any audit: excluded namespaces cannot produce useful verdicts.

## Validation and remaining gates

Every workstream passed the required `validate-pr.sh` checks: flate, shellcheck,
secret guardrails and 24 guardrail regression tests. Additional checks covered
the live Cilium schema, current selectors, chart rendering where additive
policies were replaced, and representative allowed/denied traffic paths.
Cross-review caught and corrected the Nextcloud-to-Notify-Push ingress gap and
vmagent's overly broad node access through the `cluster` entity. SMTP uses the
rendered Nextcloud port 465; its STARTTLS configuration still needs runtime
mail validation. These are local checks, not enforcement or application proof.

Important unresolved dimensions are documented per namespace: incomplete and
truncated capture; cold startup, backups, notifications and other rare paths;
Ghostfolio/Kritika direct-database exceptions and the same-node primary VIP;
ToolHive/Hermes/Headroom unexplained paths; broad dynamic metrics/P2P needs;
and evaluator support for entities, service accounts, external Services and
DNS/L7 rules. An external reverse-DNS lookup for unexplained ToolHive HTTPS was
blocked by automatic approval review due to potential cluster-metadata disclosure.
It was not performed; no destination was invented or granted from that guess.

Kritika's six read-only MCP tools and pinned feature implementation were also
reviewed. The [Kritika draft](kguardian-kritika-policy-draft.md) records historical
finding applicability, runner/indexing/tool/cache paths, and the fresh
PgBouncer startup-protocol evidence. Both poolers still reject its startup
parameters; session semantics also rule out transaction pooling.

The Phase 2 implementation plan is consolidated into this reference. All local
drafting and validation steps are complete. Phase 3 has not started: prepare
AuditNetworkPolicy resources only after the owner's next-phase confirmation,
keep deployment approval explicit, then collect at least seven days and resolve
every legitimate WouldDeny with evidence. Enforce only with a later owner OK.

Suggested continuation: "Prepare audit manifests for these eight drafts; keep
deployment gated on my approval." Verify branch and worktree before any work.
