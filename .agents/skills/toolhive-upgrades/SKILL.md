---
name: toolhive-upgrades
description: >-
  Guides Stacklok ToolHive GitHub release mining, manifest migration for Flux GitOps
  (MCPServer, VirtualMCPServer, MCPRegistry, OCI Helm pins), local audit/validate, and a
  mandatory code-reviewer subagent before completion. Use when bumping toolhive-operator
  or operator-crds tags, reconciling CRDs, fixing admission errors after upgrade, or when
  the user mentions ToolHive versions, release notes, deprecations, or CRD migrations.
compatibility: Repository uses `mise`, `rg` (ripgrep), `git`, and `bash` for scripted audit; cluster apply is user-approved per AGENTS.md.
---

# ToolHive upgrades (operator + CRDs)

## When to use

- Bumping **`ref.tag`** on ToolHive OCI charts or editing manifests under `kubernetes/apps/ai/toolhive/`.
- Investigating **admission / validation** failures after a ToolHive upgrade.
- User asks to **stay current** with ToolHive **breaking changes**, **deprecations**, or **release notes**.

## Goal

Stay ahead of **ToolHive** churn: each minor often changes CRD shapes, Helm values, or runtime behavior. Workflows here are **release-driven**, **CRD-first**, and **grep-verified** against this repo’s paths.

## This repository (path anchor)

- **Helm OCI pins:** `kubernetes/apps/ai/toolhive/app/ocirepository.yaml`, `kubernetes/apps/ai/toolhive/crds/ocirepository.yaml` (`ref.tag`).
- **Workload manifests:** `kubernetes/apps/ai/toolhive/config/*.yaml` (`MCPServer`, `VirtualMCPServer`, `MCPGroup`, `MCPServerEntry`, …).
- **Legacy doc (may lag):** `docs/ai/toolhive-v0.15-compatibility.md` — treat **GitHub releases** as source of truth for the target tag.

## Workflow (do in order)

1. **Read the target release** — `https://github.com/stacklok/toolhive/releases/tag/vX.Y.Z` and compare from the previous pinned tag (Breaking → Deprecations → Improvements).
2. **Skim repo history** — [references/breaking-history.md](references/breaking-history.md); append one row when a migration will recur.
3. **Bump pins** — Same **X.Y.Z** on both OCI `ref.tag` values unless upstream documents otherwise.
4. **Patch manifests** — Apply renames, structs, and removals from the release; avoid drive-by edits.
5. **Audit** — `bash .agents/skills/toolhive-upgrades/scripts/audit-toolhive-yaml.sh` (requires `rg`). Fix or document false positives.
6. **Validate** — `mise exec -- kubeconform -strict kubernetes/`; `mise exec -- shellcheck` on touched shell.
7. **Subagent review (blocking)** — [Review gate](#review-gate-subagent-required-before-done). Do **not** call the upgrade done until **PASS**.
8. **Cluster reconcile order** — CRD release before operator; **ask user** before live apply (`AGENTS.md`).

## Progressive disclosure

- First time on a large jump: [references/upgrade-workflow.md](references/upgrade-workflow.md).
- Uncertain YAML keys: inspect upstream CRDs at the **exact** git tag under `deploy/charts/.../crds/`.

## After the upgrade

- Extend [references/breaking-history.md](references/breaking-history.md) for new stable patterns.
- Keep **AGENTS.md** short; durable facts → `.agents/learned-workspace.md` or continual-learning.

## Scripts

- [scripts/audit-toolhive-yaml.sh](scripts/audit-toolhive-yaml.sh)

## Review gate (subagent — required before done)

**When:** After steps 5–6; **before** telling the user the upgrade is complete or merge-ready.

**How:** Spawn **code-reviewer** (e.g. Task → `subagent_type: code-reviewer`). Use the copy-paste template in [references/reviewer-handoff.md](references/reviewer-handoff.md): fill `VOLD` / `VNEW`, diff stat, audit output.

**After:** **FAIL** → fix blockers, re-run 5–6 as needed, **one** follow-up review. **PASS** → done (cluster apply still **ask first**).

Standard format reference: [agentskills.io](https://agentskills.io/).
