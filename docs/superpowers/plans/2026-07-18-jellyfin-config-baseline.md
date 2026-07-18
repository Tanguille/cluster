# Jellyfin Configuration Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Jellyfin runtime defaults by removing stale environment tuning while preserving verified Kubernetes, GPU, storage, and plugin behavior.

**Architecture:** Keep the HelmRelease focused on Kubernetes concerns and let Jellyfin own its documented runtime defaults and Dashboard-managed VA-API setting. The change is a single manifest cleanup: it removes only settings with no current evidence of benefit and leaves the security wrapper, device allocation, probes, resources, and persistence intact.

**Tech Stack:** Flux HelmRelease v2, bjw-s app-template, Jellyfin 10.11.11, Kubernetes, YAML.

---

## File structure

- Modify: `kubernetes/apps/media/jellyfin/app/helmrelease.yaml` — remove stale supplemental groups and unverified runtime overrides.
- Create: `docs/superpowers/specs/2026-07-18-jellyfin-config-baseline-design.md` — approved design record (already committed).
- Create: `docs/superpowers/plans/2026-07-18-jellyfin-config-baseline.md` — this implementation plan.

### Task 1: Remove stale Jellyfin overrides

**Files:**
- Modify: `kubernetes/apps/media/jellyfin/app/helmrelease.yaml:31-35,69-83`

- [ ] **Step 1: Confirm the worktree starts from the approved design**

Run:

```bash
git status --short
git log -1 --oneline
```

Expected: the design commit is `6fa30c110`; before Task 2, the only uncommitted
file is this implementation-plan document.

- [ ] **Step 2: Remove stale supplemental groups and runtime tuning**

Leave this exact supplemental-groups block:

```yaml
supplementalGroups:
  - 44 # video
  - 226 # render: required for VAAPI on /dev/dri/renderD*
```

Leave this exact application environment block:

```yaml
env:
  TZ: ${TIMEZONE}
```

Delete only these keys and their associated comments:

```yaml
          - 109
          - 100
JELLYFIN_FFmpeg__probesize
JELLYFIN_FFmpeg__analyzeduration
JELLYFIN_FFmpeg__fflags
JELLYFIN_FFmpeg__threads
JELLYFIN_FFmpeg__hwaccel
JELLYFIN_FFmpeg__timeout
JELLYFIN_FFmpeg__imageExtractionTimeout
DOTNET_SYSTEM_IO_DISABLEFILELOCKING
DOTNET_GCAllowVeryLargeObjects
DOTNET_GCLOHThreshold
```

Do not change the root-to-UID-568 command, `fsGroup`, groups `44` and `226`,
AMD affinity, `squat.ai/dri`, probes, resources, or persistence.

- [ ] **Step 3: Run a focused YAML syntax check**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('kubernetes/apps/media/jellyfin/app/helmrelease.yaml')); print('YAML parse passed')"
git diff --check
```

Expected: `YAML parse passed` and no diff-check output.

- [ ] **Step 4: Inspect the focused diff**

Run:

```bash
git diff -- kubernetes/apps/media/jellyfin/app/helmrelease.yaml
```

Expected: only the approved groups, environment variables, and their comments
are removed.

### Task 2: Render, review, and commit the manifest cleanup

**Files:**
- Modify: `kubernetes/apps/media/jellyfin/app/helmrelease.yaml`
- Create: `docs/superpowers/plans/2026-07-18-jellyfin-config-baseline.md`

- [ ] **Step 1: Run repository Kubernetes validation**

Run:

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Expected: the changed manifest is rendered when `flate` or `kustomize` is
available. If this environment lacks those tools, record that limitation and
retain the successful focused YAML validation from Task 1.

- [ ] **Step 2: Perform focused review**

Run:

```bash
git diff --check
git diff -- kubernetes/apps/media/jellyfin/app/helmrelease.yaml
git status --short
```

Confirm that the diff restores Jellyfin defaults, adds no secrets, and leaves
VA-API device access and plugin startup behavior unchanged.

- [ ] **Step 3: Commit the approved change**

Run:

```bash
git add \
  kubernetes/apps/media/jellyfin/app/helmrelease.yaml \
  docs/superpowers/plans/2026-07-18-jellyfin-config-baseline.md
git commit -m "fix(jellyfin): restore runtime defaults"
```

Expected: a Conventional Commit containing only the manifest cleanup and its
implementation plan. The earlier approved design remains in its own commit.

- [ ] **Step 4: Push the branch and create the pull request**

Run:

```bash
git push -u origin fix/jellyfin-config-baseline
gh pr create \
  --base main \
  --head fix/jellyfin-config-baseline \
  --title "fix(jellyfin): restore runtime defaults" \
  --body-file /tmp/jellyfin-config-baseline-pr.md
```

Use a PR body that summarizes the removed stale overrides, the representative
media comparison, retained VA-API configuration, validation results, and the
fact that no live reconcile was performed.
