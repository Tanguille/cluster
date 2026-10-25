# PR shepherding: pre-push gates and CI triage

Shepherd = carry a PR to green CI + clean automated review, iterating, without orphaning it.
Extends `pr-review`. This file is the SINGLE SOURCE OF TRUTH for the loop — the cron prompt and
learned-preferences only point here.

## Route: ToolHive MCP — the PR tools

General GitHub routing, call shape and fallbacks: `.agents/learned-workspace.md` § ToolHive / MCP.
The PR-specific half:

| Job | Tool |
|---|---|
| Kritika head / reviews / unfinished job / retry and failure reasons | `kritika_get_pull {repository, number}` |
| One Kritika review's structured findings and confidence | `kritika_get_review {repository, reviewId}` |
| Open-PR board | `github_list_pull_requests` (state=open) |
| Details / status / checks / comments / diff / files / commits | `github_pull_request_read` (method=…) |
| Edit title / body / draft / state / reviewers | `github_update_pull_request` |
| Rebase PR onto base (server-side) | `github_update_pull_request_branch` |

Verified quirks (2026-09-07):

- Selector is `pullNumber`, not `number`.
- `create_pull_request` ignores `draft: true` → follow with `update_pull_request {pullNumber, draft: true}`.

Kritika's tools are read-only. Keep GitHub for CI, human/other-bot feedback, replies,
thread resolution and review commands (`@tanguille-cluster review` for this repository).

## Gates

**A — Rebase before every push.** Primary: `github_update_pull_request_branch` (no local clone).
Only on conflict: worktree from PR head → `git fetch origin --prune` (local checkout is stale — never trust the working tree) → `git rebase origin/main` (merge-reconciler for semantic conflicts) → last-resort push. Non-trivial conflict → stop, hand off (handoff skill), don't guess.

**B — Description grounded in the diff.** Every factual claim in the body (ports, tags, commands,
behavior, files) must have a diff line behind it (`get_diff`). Mismatch → fix body or code; never
"clarify in review". Say what the PR does NOT change when a sibling area is easy to confuse.

**C — Atomic finish.** Plan the commits (`.agents/learned-workspace.md` GitHub table) + PR create/update as ONE step; if it can't be done
this turn, follow the [handoff skill](../../handoff/SKILL.md).

## CI triage — classify before touching the diff

1. Read the failure: `get_check_runs` for names/conclusions; job logs via `github_get_job_logs`.
2. Classify exactly one:
   - **diff-introduced** → fix files (read current → edit → commit per that table), re-check.
   - **baseline/environment** → prove it fails on `origin/main` too (`github_actions_list`, branch main), then
     STOP fixing the diff; comment the evidence, wait or report.
   - **flake** → re-run ONCE. Second failure on the same job is not a flake — re-classify.
3. Record the classification on the PR (one line) so the next agent doesn't re-diagnose.

Budget: max **3 fix-push cycles per failure class**, then escalate with the evidence.

## Shepherd loop (per open owner-PR)

1. Call `kritika_get_pull` first, then refresh GitHub's head, CI and human/other-bot feedback.
   Compare Kritika's `headSha` with GitHub's head; do not treat lagging state as current.
2. DIRTY → Gate A.
3. CI red → triage above.
4. For each newly completed Kritika review of the current head, call `kritika_get_review`
   and remember its id. Re-fetch that id only to verify changed finding/dismissal state;
   do not repeatedly fetch GitHub comment history for Kritika findings.
5. Triage Kritika findings and GitHub feedback, including CodeRabbit (login startswith
   `coderabbitai`): fix valid issues, dismiss false positives with evidence, then push fixes.
6. An unfinished Kritika job for the current head, including scheduled/running jobs and
   pending retries, means wait; continue independent CI/feedback work without a duplicate
   review command. If no job remains, inspect the failure/skip/paused reason before retrying
   through GitHub, and refresh `kritika_get_pull` immediately before requesting a review.
7. Body contradicts diff → Gate B.
8. Require the latest Kritika review of GitHub's current head to be `completed`, with blocking
   findings resolved and configured confidence gates passed. Superseded, skipped, failed,
   canceled or unfinished reviews are not approval, even if a GitHub status is green.
   Confirm the current-head GitHub review status and, when automatic approval is enabled,
   the bot's approval; `completed` alone is insufficient.
9. Green + clean + draft → `update_pull_request {pullNumber, draft: false}`; report merge-ready.

Stop: green+clean, escalated, or budget exhausted.

**Boundaries (never, even "automatically"):** no merge, no push to `main`, no force-push, no
cluster apply/reconcile, no secret decryption, no touching non-owner PRs (renovate/dependabot/
other users have their own automation). Merging is a human decision.
