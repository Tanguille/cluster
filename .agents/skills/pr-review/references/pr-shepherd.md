# PR shepherding: pre-push gates and CI triage

Reviewing a PR (phases 1–6) is not the same as shepherding it. Shepherd = carry a PR from
branch to green CI + clean automated review, iteratively, without orphaning it. These
procedures extend `pr-review`; use them on any PR being advanced toward merge.

## Tooling: ToolHive MCP `github_*` tools (the route)

ALL GitHub work goes through ToolHive MCP: `mcp__toolhive__call_tool` with
`tool_name` = `github_<name>` (the prefix is mandatory — unprefixed names return
"tool not found"). The agent box has **no** `gh` and **no** `GITHUB_TOKEN`, and the
`pushremote` mirror is dead — never `git push origin` or `git push pushremote`.

| Job | Tool |
|---|---|
| Open-PR board | `github_list_pull_requests` (state=open; fields: number, created_at, mergeable_state, draft, head, user, title) |
| PR details / status / checks / comments / diff | `github_pull_request_read` (method: `get`, `get_status`, `get_check_runs`, `get_comments`, `get_review_comments`, `get_diff`, `get_files`, `get_commits`) |
| Edit PR title / body / draft / state / reviewers | `github_update_pull_request` |
| Rebase PR onto updated base | `github_update_pull_request_branch` (server-side; no local clone needed) |
| Commit to a branch | `github_push_files` (full file contents; one commit per call) |
| Delete a file | `github_delete_file` (`push_files` has no delete — pair them) |
| New branch | `github_create_branch` |

Verified quirks (2026-09-07):

- `github_create_pull_request` **ignores** `draft: true` — call `github_update_pull_request
  {pullNumber, draft: true}` immediately after creation.
- `github_push_files` **bypasses local pre-commit hooks** and takes FULL file contents.
  For code changes hooks would police, verify locally in a worktree before pushing the
  new contents. For docs-only changes it is the normal route.
- ToolHive has **no workflow re-run or job-log tool**. The only two remaining ssh-gh
  fallbacks (the server runs fish; it has an authed `gh` via `~/cluster`):
  `ssh -o BatchMode=yes tanguille@192.168.0.181 'gh run view <id> --repo Tanguille/cluster --log-failed'`
  and `... 'gh run rerun <id> --repo Tanguille/cluster --failed'`.
- `github_pull_request_read`'s PR selector is `pullNumber` (not `number`);
  `github_push_files` takes `files: [{path, content}]` + `message` (not `commit_message`).
- Degraded ToolHive envelope: check `agent.log` FIRST (an "unknown argument" error is a
  cron model-routing bug, not an outage), then `~/.hermes/scripts/toolhive_retry.py
  call <tool> <json>` (flags like `--max-retries` go BEFORE the positionals).
- The wrapper's SDK client crashes on large payloads (known SSE/TaskGroup bug) even when
  the server succeeds — trust the raw HTTP JSON-RPC fallback it prints.
- Last-resort git route (only for conflict resolution that must go through git locally):
  worktree commit → `git bundle create` → `scp` to the server → fetch+push from
  `~/cluster` there (authed gh). Documented here, used rarely.

Reading current file contents before an edit: `web_extract` on
`https://raw.githubusercontent.com/Tanguille/cluster/<branch>/<path>`, or
`github_pull_request_read` method=`get_files` / `get_diff` for context.

## Gate A — Rebase discipline (before every push)

Branches left behind `origin/main` accumulate rebase debt; renovate merges land ahead and
conflicts surface mid-PR (e.g. toolhive-resourceset, where an upstream bump collided with a
pending change). Before pushing ANY branch:

1. **Primary**: `github_update_pull_request_branch` (server-side merge of base into head;
   pass `expectedHeadSha` when you know it). No local clone, no bundle.
2. **If it fails on conflict**: resolve locally — worktree from the PR head,
   `git fetch origin --prune` (the checkout is stale; never trust the working tree),
   `git rebase origin/main`, resolve with the merge-reconciler skill, then push via the
   last-resort git route above. If the conflict is non-trivial (semantic, not cosmetic),
   stop and hand off (handoff skill) instead of guessing.

Done when the branch contains only its own changes on top of current `origin/main`.

## Gate B — Description grounded in the diff (before opening or editing a PR)

A wrong PR description is worse than no description: it actively misleads reviewers
(verified failure: a PR body claimed a port change and a bare-binary command that the
manifest did not contain — caught by CodeRabbit). Before posting or editing a PR body:

1. Pull the diff: `github_pull_request_read` method=`get_diff` (or `git diff
   origin/main...HEAD` in a worktree).
2. For EVERY factual claim in the body (ports, image tags, commands, flags, behavior
   changes, files touched), locate the line in the diff that proves it.
3. Any claim the diff does not support: fix the body, or fix the code. Never leave the
   mismatch and "clarify in review".
4. State what the PR does NOT change when a sibling area is easy to confuse (e.g. "port
   stays 6767"). Apply via `github_update_pull_request {pullNumber, body}`.

Done when every claim in the body has a diff line behind it.

## Gate C — Atomic finish (before the turn ends)

The two costliest historical failures were tasks that finished everything except the final
"push + open PR" because the session ended or an approval gate landed mid-task.

- Plan **push + open/advance PR as one step** (one command block: `github_push_files`
  then PR create/update).
- If it cannot be done now, write `HANDOFF.md` + `PLAN.md` (handoff skill) before
  stopping — never leave the finish implicit in chat.
- On resume, read `HANDOFF.md` first; do not re-plan from compressed context.

## CI triage — classify before touching the diff (on every failure)

"Keep adjusting the PR until CI passes" is the wrong loop when the failure is not the
diff's fault. The first move on ANY red check is classification, not a code change:

1. **Read the failure**: `github_pull_request_read` method=`get_check_runs` for
   names/conclusions; for job logs use the only available route —
   `ssh -o BatchMode=yes tanguille@192.168.0.181 'gh run view <id> --repo Tanguille/cluster
   --log-failed'` (run id from the check run's external id, or `gh run list --branch <head>`).
2. **Classify** exactly one of:
   - **diff-introduced** — the changed code/config triggers it. Fix the file(s) (read
     current contents → edit → `github_push_files` with FULL new contents), re-check.
   - **baseline/environment** — it fails on `origin/main` too (e.g. a shared lint job
     failing across the repo, an image pull hiccup, a registry outage). Prove it: check
     the latest main build (`ssh ... 'gh run list --repo Tanguille/cluster --branch main
     --limit 1'`). Then STOP fixing the diff; comment the evidence on the PR and wait or
     report — do not churn the diff to satisfy a non-diff failure.
   - **flake** — intermittent, identical input. Re-run ONCE:
     `ssh ... 'gh run rerun <id> --repo Tanguille/cluster --failed'` (no ToolHive tool).
     A second failure on the same job is not a flake; re-classify.
3. Record the classification in the PR thread (one line: "baseline — main build #N same
   failure; not diff-related") so the next agent does not re-diagnose from scratch.

Iteration budget: at most **3 fix-push cycles per distinct failure class** before
escalating to the user with the classification evidence. Silent infinite "adjust until
green" is a bug, not a feature.

## Shepherd loop (the full cycle, per PR)

```
for each open owner-PR (github_list_pull_requests, state=open):
  1. if mergeable_state=DIRTY: Gate A (server-side branch update; local rebase only on conflict)
  2. if CI red (get_status/get_check_runs): classify → fix via github_push_files / comment / re-run once
  3. if CodeRabbit (get_comments, user.login starts with "coderabbitai") has unresolved
     blocking findings: address them, push, re-run review
  4. if PR body contradicts get_diff: fix body (Gate B)
  5. when CI green AND review clean: mark ready (github_update_pull_request {draft:false}
     if still draft), summarize state
stop condition: green+clean, OR escalated with evidence, OR budget exhausted
```

Boundaries (never cross, even "automatically"): no merge, no push to `main`, no
force-push, no cluster apply/reconcile, no secret decryption, no touching PRs owned by
others (renovate/dependabot/other users have their own automation). Merging and applying
are human decisions.
