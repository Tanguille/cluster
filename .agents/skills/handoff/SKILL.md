---
name: handoff
description: >-
  Write and resume durable work handoffs so tasks that cross a session, compaction, or
  approval boundary are never lost — especially the "push + open PR" finish of coding tasks.

  user: "continue / finish / where did we leave off" → read the handoff file first, then resume
  agent ends a turn with unpushed, unfinished work → write `.agents/handoff/<task>.md` before stopping
  user: "why did the PR never get opened?" → the finish line was orphaned; this skill prevents it

  Load before ending ANY turn in which work is verified but not yet pushed or published.
compatibility: Requires `git`. No network tools needed.
---

# Work handoff

Tasks repeatedly died at the finish line: the work was built and verified, but the final two
steps (push + open PR) were lost when the session ended, context was compacted, or an approval
gate landed mid-task. This skill makes the handoff a **named, written, resumable object**
instead of a hope that the next agent re-derives it from a compacted summary.

## The two rules

1. **Never end a turn with verified-but-unpushed work.** If you cannot finish the push +
   publish steps now, you MUST write `.agents/handoff/<task>.md` BEFORE stopping, and tell the
   user exactly what was left. That directory is gitignored, so the file cannot ride along in a
   commit, and each worktree carries its own.
2. **Resuming always starts by reading the handoff file** — not by re-deriving state from
   conversation history or a compressed summary. If one exists for the same task, its
   "Remaining steps" section is the plan; do not re-plan.

## When to write a handoff

- Any turn ending with: unpushed commits, unopened PR, unapplied config, unreported result.
- Context compaction is imminent (long multi-step task, large tool outputs).
- An approval gate will land before the work is complete (push, apply, delete).
- Delegating the rest of the task to another agent, session, or cron run.

## Format

Keep it under ~80 lines. Facts and commands, not narrative. Checkboxes carry the progress, so
there is no second file to keep in sync:

```markdown
# HANDOFF — <task name>

**Updated:** <ISO timestamp> | **Agent:** <who> | **Status:** in-progress

## Where things stand
- Repo / branch / worktree: <path>, <branch>
- Verified so far (with evidence): <tests passed, output, PR number, SHA>

## Remaining steps (exact, in order)
- [x] <finished step> — <evidence>
- [ ] <command or action — copy-paste runnable>
- [ ] <next action>

## Resume pointer
First action for the next agent: <the single first step, e.g. "run the first unchecked step">

## Blockers / decisions needed
- <none | open question for the user>
```

## The atomic finish rule

Treat **push + open PR as ONE step**. Plan the turn so a single approval covers the whole
finish, rather than leaving the agent one approval-boundary away from done. Use whichever push
route the repo actually has (this one pushes through ToolHive, not local `git push` — see
`.agents/learned-workspace.md` § ToolHive / MCP). If the push target is ambiguous, decide it up
front and record it in the handoff — do not leave it for the resuming agent to rediscover.

## Pitfalls

- Writing the handoff AFTER the turn ends — impossible by definition; write it before stopping.
- Leaving "TODO: finish PR" in chat text without a file — chat context is the thing that gets
  compacted; the file is the durable object.
- Re-planning on resume. The handoff says what remains; verify it, then execute it.

## Verification

- [ ] `.agents/handoff/<task>.md` exists with exact, copy-paste commands for every remaining step.
- [ ] Verified-so-far section cites real evidence (SHAs, test output, PR numbers).
- [ ] User was told what remains and what the first resume step is.
