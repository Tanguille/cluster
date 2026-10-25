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

1. Refresh `kritika_get_pull`, then GitHub's head, CI and human/other-bot feedback.
   Match Kritika's `headSha` to GitHub's head before using its results.
2. Fetch `kritika_get_review` once per newly completed current-head review id;
   re-fetch only when finding/dismissal state changes. Use these findings instead
   of GitHub comment history.
3. Fix valid findings; dismiss false positives with evidence. Triage CI as above
   and apply Gates A–C before pushing.
4. If Kritika has an unfinished current-head job, wait while handling independent work.
   Before any review command, refresh `kritika_get_pull`: require no current-head job
   and inspect the failure/skip/paused reason.
5. Ready requires green CI, all feedback triaged, and the latest current-head Kritika
   review `completed` with no blocking findings, passing configured confidence/status gates and
   the bot's approval when enabled. Older reviews and all other states, including
   skipped/superseded/failed, do not count as approval.
6. When ready, mark a draft ready with `update_pull_request {pullNumber, draft: false}`
   and report merge-ready.

Stop: green+clean, escalated, or budget exhausted.

**Boundaries (never, even "automatically"):** no merge, no push to `main`, no force-push, no
cluster apply/reconcile, no secret decryption, no touching non-owner PRs (renovate/dependabot/
other users have their own automation). Merging is a human decision.
