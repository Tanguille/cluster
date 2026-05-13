---
name: toolhive-upgrades
description: >-
  Guides Stacklok ToolHive upgrades: compare pinned tags to upstream main and to release notes,
  manifest migration for Flux GitOps (MCPServer, VirtualMCPServer, MCPRegistry, OCI Helm pins),
  local audit/validate, and a mandatory code-reviewer subagent before completion. Use when
  bumping toolhive-operator or operator-crds tags, reconciling CRDs, fixing admission errors
  after upgrade, or when the user mentions ToolHive versions, release notes, deprecations,
  or CRD migrations.
compatibility: Repository uses `mise`, `rg` (ripgrep), `git`, and `bash` for scripted audit; cluster apply is user-approved per AGENTS.md.
---

# ToolHive upgrades (operator + CRDs)

## When to use

- Bumping **`ref.tag`** on ToolHive OCI charts or editing manifests under `kubernetes/apps/ai/toolhive/`.
- Investigating **admission / validation** failures after a ToolHive upgrade.
- User asks to **stay current** with ToolHive **breaking changes**, **deprecations**, or **release notes**.

## Goal

Stay ahead of **ToolHive** churn: each minor often changes CRD shapes, Helm values, or runtime behavior. Workflows are **`main`-aware**, **semver-grounded**, **release-backed when tagging**, **CRD-first**, and **grep-verified** against this repo’s paths.

**Normalize refs before GitHub:** Flux **`ref.tag`** is usually **`X.Y.Z`** **without** a leading **`v`**; upstream GitHub **release tags are `vX.Y.Z`**. **`compare/0.26.0...main`** does **not** resolve correctly — **always use `v${PIN}`** in GitHub URLs and raw **`VERSION`** URLs under **`refs/tags/vX.Y.Z`**.

**Ground truth vs compare graphs:** Read **`VERSION`** on **`main`** and at your target **`v`** tag (`https://raw.githubusercontent.com/stacklok/toolhive/main/VERSION`, etc.). **`Latest Release`**, **`main` VERSION**, and your pin can disagree depending on maintainer workflow — never infer currency from **`ahead_by` / `behind_by`** alone when refs were wrong or branching differs.

## This repository (path anchor)

- **Helm OCI pins:** `kubernetes/apps/ai/toolhive/app/ocirepository.yaml`, `kubernetes/apps/ai/toolhive/crds/ocirepository.yaml` (`ref.tag`).
- **Workload manifests:** `kubernetes/apps/ai/toolhive/config/*.yaml` (`MCPServer`, `VirtualMCPServer`, `MCPGroup`, `MCPServerEntry`, …).
- **Legacy doc (may lag):** `docs/ai/toolhive-v0.15-compatibility.md` — pair **GitHub releases** (for a shipped tag) with **`main`** (for churn since that tag).

## Workflow (do in order)

1. **Pin ↔ upstream `main` / releases — normalize + semver first**
   - Run **`bash .agents/skills/toolhive-upgrades/scripts/upstream-pin-vs-main.sh`** (optional `[PIN]`). This prints **`VERSION` on `main`**, **`VERSION` at `vPIN`**, **Latest Release**, correct **`compare`** URLs (**with `v` prefix**), and REST **`ahead_by` / `behind_by`** for both directions.
   - **Never** paste Flux **`ref.tag`** bare into **`github.com/.../compare/...`** — map **`PIN → vPIN`** first (same mapping as **`OCI`** chart tags ↔ **`git tag`**).
   - **When bumping to a shipped tag:** Read **`https://github.com/stacklok/toolhive/releases/tag/vX.Y.Z`** and compare **`vOLD...vNEW`** (Breaking → Deprecations → Improvements).
   - **When deliberately tracking `main`:** Confirm **`deploy/charts/operator/`** `version:` / **`VERSION`** at **`main`** tip match your risk tolerance (document if pre-release).
2. **Skim repo history** — [references/breaking-history.md](references/breaking-history.md); append one row when a migration will recur.
3. **Bump pins** — Same **X.Y.Z** on both OCI `ref.tag` values unless upstream documents otherwise.
4. **Patch manifests** — Apply renames, structs, and removals from the release; avoid drive-by edits.
5. **Audit** — `bash .agents/skills/toolhive-upgrades/scripts/audit-toolhive-yaml.sh` (requires `rg`). Fix or document false positives.
6. **Validate** — `mise exec -- kubeconform -strict kubernetes/` (expect Flux/CRD/SOPS noise without extra schemas); `mise exec -- shellcheck` on touched shell. For ToolHive-only YAML without bundled CRD schemas:
   `mise exec -- kubeconform -strict -ignore-missing-schemas -ignore-filename-pattern 'secret\.sops\.yaml' kubernetes/apps/ai/toolhive/`.
7. **Subagent review (blocking)** — [Review gate](#review-gate-subagent-required-before-done). Do **not** call the upgrade done until **PASS**.
8. **Cluster reconcile order** — CRD release before operator; **ask user** before live apply (`AGENTS.md`).

## Progressive disclosure

- First time on a large jump: [references/upgrade-workflow.md](references/upgrade-workflow.md).
- Uncertain YAML keys: inspect upstream CRDs under `deploy/charts/.../crds/` at the **exact git tag** you deploy; if assessing **`main`** churn, use **`main`** for that inspection instead of the stale tag.

## After the upgrade

- Extend [references/breaking-history.md](references/breaking-history.md) for new stable patterns.
- Keep **AGENTS.md** short; durable facts → `.agents/learned-workspace.md` or continual-learning.

## Scripts

- [scripts/upstream-pin-vs-main.sh](scripts/upstream-pin-vs-main.sh) — **`VERSION`** + normalized **`v`** compares (run step 1).
- [scripts/audit-toolhive-yaml.sh](scripts/audit-toolhive-yaml.sh)

## Review gate (subagent — required before done)

**When:** After steps 5–6; **before** telling the user the upgrade is complete or merge-ready.

**How:** Spawn **code-reviewer** (e.g. Task → `subagent_type: code-reviewer`). Use the copy-paste template in [references/reviewer-handoff.md](references/reviewer-handoff.md): fill `VOLD` / `VNEW`, diff stat, audit output.

**After:** **FAIL** → fix blockers, re-run 5–6 as needed, **one** follow-up review. **PASS** → done (cluster apply still **ask first**).

Standard format reference: [agentskills.io](https://agentskills.io/).
