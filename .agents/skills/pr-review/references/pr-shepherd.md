# PR shepherding: pre-push gates and CI triage

Reviewing a PR (phases 1–6) is not the same as shepherding it. Shepherd = carry a PR from
branch to green CI + clean automated review, iteratively, without orphaning it. These
procedures extend `pr-review`; use them on any PR being advanced toward merge.

## Gate A — Rebase discipline (before every push)

Branches left behind `origin/main` accumulate rebase debt; renovate merges land ahead and
conflicts surface mid-PR (e.g. toolhive-resourceset, where an upstream bump collided with a
pending change). Before pushing ANY branch:

```bash
git fetch origin --prune
# in the worktree for the branch:
git rebase origin/main          # or merge origin/main if the repo convention prefers
# if conflict: resolve with the merge-reconciler skill, verify `git status` clean, continue
```

Done when the branch contains only its own changes on top of current `origin/main`. If a
rebase conflict is non-trivial (semantic, not cosmetic), stop and hand off (handoff skill)
instead of guessing.

## Gate B — Description grounded in the diff (before opening or editing a PR)

A wrong PR description is worse than no description: it actively misleads reviewers (verified
failure: a PR body claimed a port change and a bare-binary command that the manifest did not
contain — caught by CodeRabbit). Before posting or editing a PR body:

1. `git diff origin/main...HEAD --stat` and read the full diff (or `gh pr diff <n>`).
2. For EVERY factual claim in the body (ports, image tags, commands, flags, behavior changes,
   files touched), locate the line in the diff that proves it.
3. Any claim the diff does not support: fix the body, or fix the code. Never leave the
   mismatch and "clarify in review".
4. State what the PR does NOT change when a sibling area is easy to confuse (e.g. "supervisor
   command unchanged", "port stays 6767").

Done when every claim in the body has a diff line behind it.

## Gate C — Atomic finish (before the turn ends)

The two costliest historical failures were tasks that finished everything except the final
"push + open PR" because the session ended or an approval gate landed mid-task.

- Plan **push + open PR as one step** (one command block: `git push` then PR create).
- If it cannot be done now, write `HANDOFF.md` + `PLAN.md` (handoff skill) before stopping —
  never leave the finish implicit in chat.
- On resume, read `HANDOFF.md` first; do not re-plan from compressed context.

## CI triage — classify before touching the diff (on every failure)

"Keep adjusting the PR until CI passes" is the wrong loop when the failure is not the diff's
fault. The first move on ANY red check is classification, not a code change:

1. **Read the failure**: check run annotations/summary (GitHub Actions: `gh run view
   --log-failed` or check-run annotations via API; ToolHive cannot fetch job logs — see the
   github-operations skill).
2. **Classify** exactly one of:
   - **diff-introduced** — the changed code/config triggers it. Fix the diff, push, re-check.
   - **baseline/environment** — it fails on `origin/main` too (e.g. a shared lint job failing
     across the repo, an image pull hiccup, a registry outage). Prove it: run the same check
     against `origin/main` (or compare with the latest main build). Then STOP fixing the diff;
     comment the evidence on the PR and wait or report — do not churn the diff to satisfy a
     non-diff failure.
   - **flake** — intermittent, identical input. Re-run the job ONCE. A second failure on the
     same job is not a flake; re-classify.
3. Record the classification in the PR thread (one line: "baseline — main build #N same
   failure; not diff-related") so the next agent does not re-diagnose from scratch.

Iteration budget: at most **3 fix-push cycles per distinct failure class** before escalating
to the user with the classification evidence. Silent infinite "adjust until green" is a bug,
not a feature.

## Shepherd loop (the full cycle, per PR)

```
for each open PR:
  1. fetch + rebase (Gate A) if branch behind main or PR is in conflict
  2. if CI red: classify (Gate C-triage) → fix diff / comment / re-run
  3. if automated review (e.g. CodeRabbit) has unresolved blocking findings:
     address them, push, re-run review
  4. if PR body contradicts diff: fix body (Gate B)
  5. when CI green AND review clean: mark ready (undraft), summarize state
  stop condition: green+clean, OR escalated with evidence, OR budget exhausted
```

Boundaries (never cross, even "automatically"): no merge, no push to `main`, no force-push,
no cluster apply/reconcile, no secret decryption. Merging and applying are human decisions.
