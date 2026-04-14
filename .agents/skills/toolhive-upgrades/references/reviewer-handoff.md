# Code-reviewer handoff (copy-paste)

Replace `VOLD`, `VNEW`, and paste `git diff --stat` / audit output. Send as the task prompt to `subagent_type: code-reviewer`.

---

## ToolHive upgrade review (blocking gate)

**Scope:** Stacklok ToolHive operator + GitOps manifests under `kubernetes/apps/ai/toolhive/`.

**Version jump:** `VOLD` → `VNEW` (OCI `ref.tag` on both `toolhive-operator` and `toolhive-operator-crds` must match `VNEW`).

**Release:** <https://github.com/stacklok/toolhive/releases/tag/VNEW>
**Compare:** <https://github.com/stacklok/toolhive/compare/VOLD...VNEW>

### Diff summary

(paste output of `git diff --stat`)

### Audit script

(paste output of `bash .agents/skills/toolhive-upgrades/scripts/audit-toolhive-yaml.sh`)

### Checklist — reply BLOCKER or OK per row

1. Every **Breaking change** in the `VNEW` release for this jump is in the diff or **N/A** with one-line repo-specific reason.
2. Both OCIRepositories use `ref.tag: VNEW` (and match each other unless upstream documents an exception).
3. No plaintext secrets; no unrelated churn outside ToolHive + agreed docs.
4. If `helmrelease.yaml` values changed: types match chart (e.g. `operator.env` is a list of `{name,value}`).
5. Local validation (kubeconform / shellcheck) is credible for this diff.

**Verdict:** **PASS** (no blockers) or **FAIL** (list blockers; implementer fixes and re-invokes you once).

---
