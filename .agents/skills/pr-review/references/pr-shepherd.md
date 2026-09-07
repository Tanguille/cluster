# PR shepherding: pre-push gates and CI triage

Shepherd = carry a PR to green CI + clean automated review, iterating, without orphaning it.
Extends `pr-review`. This file is the SINGLE SOURCE OF TRUTH — the cron prompt and
learned-preferences only point here.

## Route: ToolHive MCP `github_*` — all GitHub work

Call shape: `mcp__toolhive__call_tool` with `tool_name = github_<name>` (prefix mandatory),
backend params inside `parameters`. No local git push: this box has no `gh`/`GITHUB_TOKEN`.

| Job | Tool |
|---|---|
| Open-PR board | `github_list_pull_requests` (state=open) |
| Details / status / checks / comments / diff / files / commits | `github_pull_request_read` (method=…) |
| Edit title / body / draft / state / reviewers | `github_update_pull_request` |
| Rebase PR onto base (server-side) | `github_update_pull_request_branch` |
| Commit to a branch (FULL file contents, 1 commit/call) | `github_push_files` |
| Delete a file | `github_delete_file` (push_files has no delete) |
| New branch | `github_create_branch` |

Verified quirks (2026-09-07):

- Selector is `pullNumber`, not `number`; `push_files` takes `files: [{path, content}]` + `message`.
- `create_pull_request` ignores `draft: true` → follow with `update_pull_request {pullNumber, draft: true}`.
- `push_files` bypasses local hooks; for code changes verify in a worktree first.
- Read current contents before pushing (worktree, or `get_diff`/`get_files`, or raw.githubusercontent.com).
- No workflow re-run/job-log tool. Only ssh-gh fallbacks (server = fish, NO heredocs; one-liners only):
  `gh run view <id> --repo Tanguille/cluster --log-failed` · `gh run rerun <id> --repo Tanguille/cluster --failed` · `gh run list --branch main --limit 1`.
- Degraded envelope: check `agent.log` first ("unknown argument" = cron model-routing bug, not outage),
  then `~/.hermes/scripts/toolhive_retry.py call <tool> <json>` — flags BEFORE positionals; its SDK client
  crashes on large payloads even when the server succeeds, trust the raw HTTP fallback it prints.
- Last resort (conflict resolution only): worktree commit → `git bundle` → `scp` to server → push from `~/cluster` (authed gh).

## Gates

**A — Rebase before every push.** Primary: `github_update_pull_request_branch` (no local clone).
Only on conflict: worktree from PR head → `git fetch origin --prune` (local checkout is stale — never trust the working tree) → `git rebase origin/main` (merge-reconciler for semantic conflicts) → last-resort push. Non-trivial conflict → stop, hand off (handoff skill), don't guess.

**B — Description grounded in the diff.** Every factual claim in the body (ports, tags, commands,
behavior, files) must have a diff line behind it (`get_diff`). Mismatch → fix body or code; never
"clarify in review". Say what the PR does NOT change when a sibling area is easy to confuse.

**C — Atomic finish.** Plan `github_push_files` + PR create/update as ONE step. If it can't be done
this turn, write `HANDOFF.md` + `PLAN.md` (handoff skill) before stopping; on resume read HANDOFF.md
first, don't re-plan from compressed context.

## CI triage — classify before touching the diff

1. Read the failure: `get_check_runs` for names/conclusions; job logs via the ssh-gh route above.
2. Classify exactly one:
   - **diff-introduced** → fix files (read current → edit → `push_files` with full contents), re-check.
   - **baseline/environment** → prove it fails on `origin/main` too (`gh run list --branch main`), then
     STOP fixing the diff; comment the evidence, wait or report.
   - **flake** → re-run ONCE. Second failure on the same job is not a flake — re-classify.
3. Record the classification on the PR (one line) so the next agent doesn't re-diagnose.

Budget: max **3 fix-push cycles per failure class**, then escalate with the evidence.

## Shepherd loop (per open owner-PR)

1. DIRTY → Gate A. 2. CI red → triage above. 3. CodeRabbit blocking findings (login
   startswith `coderabbitai`) → address, push. 4. Body contradicts diff → Gate B.
5. Green + clean + draft → `update_pull_request {pullNumber, draft: false}`; report merge-ready.

Stop: green+clean, escalated, or budget exhausted.

**Boundaries (never, even "automatically"):** no merge, no push to `main`, no force-push, no
cluster apply/reconcile, no secret decryption, no touching non-owner PRs (renovate/dependabot/
other users have their own automation). Merging is a human decision.
