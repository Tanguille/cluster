# Phase 6: Docs/LLM Sync Check
**Completed:** 2026-03-07

## Findings

### AGENTS.md Verification

**Project Structure (Line 32):**
- ❌ Docs/ folder incorrectly lists `common-operations` and `commands` as being in docs/, but:
  - `docs/common-operations.md` does NOT exist
  - `docs/commands.md` does NOT exist
  - These files are at `.agent/common-operations.md` (and there's no commands.md equivalent)

**Commands Section (Line 36-39):**
- ✅ `task talos:generate-config` documented and exists
- ✅ `task talos:apply-node IP=...` documented and exists
- ✅ `task talos:upgrade-node IP=...` documented and exists
- ✅ Validation commands listed (kubeconform, shellcheck) - all correct
- ❌ **flux-local** referenced but NOT installed in environment

**Table (Line 20):** Formatting has extra dash causing misalignment

### docs/ Verification

- ✅ `docs/useful_commands.md` - Exists, commands match taskfile structure
- ✅ `docs/volsync-restore.md` - Exists, accurate workflow
- ✅ `docs/sops-post-quantum.md` - Exists, path_regexes match `.sops.yaml`
- ❌ `docs/commands.md` - Does NOT exist (AGENTS.md line 32 references it)
- ❌ `docs/common-operations.md` - Does NOT exist (AGENTS.md line 32 references it)

### .agent/ Files Verification

| File | Triggers (from AGENTS.md) | Actual Content Match |
|------|---------------------------|---------------------|
| learned-preferences.md | revert, undo, resources, memory, CPU, MCP vs shell | ✓ |
| learned-workspace.md | HTTPRoute, ToolHive, MCPServer, Flux, Talos, Reloader, in-cluster URL, Rook, RBAC, zap | ✓ |
| common-operations.md | add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, volsync, snapshot | ✓ |
| worktree-isolation.md | worktree, isolated work, parallel agent, experimental branch, feature branch | ✓ |

### Taskfile.yaml Verification

**Main Taskfile:**
- ✅ `reconcile` task exists with correct kustomizations
- ⚠️ Volsync include: `volsync: .taskfiles/volsync` - actual file is `taskfile.yaml` (lowercase)
- ⚠️ `upgrade-arc` task duplicated in both main and kubernetes Taskfile

**Talos Taskfile:**
- ✅ All documented tasks exist: `generate-config`, `apply-node`, `upgrade-node`, `upgrade-k8s`
- ✅ `schematics-update` task exists and documented

### .sops.yaml Verification
- ✅ Path regexes in `.sops.yaml` match `docs/sops-post-quantum.md`:
  - `talos/.*\.sops\.ya?ml`
  - `(bootstrap|kubernetes)/.*\.sops\.ya?ml`

## Sync Issues

| Type | File | Issue | Current State | Expected State |
|------|------|-------|---------------|----------------|
| High | AGENTS.md:32 | References `docs/common-operations` | File at `.agent/common-operations.md` | Fix reference path |
| High | AGENTS.md:32 | References `docs/commands.md` | Does not exist | Remove reference |
| High | AGENTS.md:38 | References `flux-local` | Not installed | Remove or install |
| Medium | AGENTS.md:32 | Lists docs contents | `useful_commands`, `common-operations`, `commands` | Update to reflect actual |
| Medium | Taskfile.yaml:23 | Volsync include case | `.taskfiles/volsync` | Should be `.taskfiles/volsync/taskfile.yaml` |
| Medium | Taskfile.yaml | upgrade-arc duplicated | In main and kubernetes Taskfile | Consolidate to one |
| Low | AGENTS.md:20 | Table separator formatting | Extra dash | Fix alignment |

## Action Items

- [ ] Fix AGENTS.md line 32: Change to `docs/ (useful_commands), .agent/ (common-operations)`
- [ ] Remove `docs/commands.md` reference from AGENTS.md line 32
- [ ] Remove `flux-local` from AGENTS.md line 38 validation commands
- [ ] Fix Taskfile.yaml volsync include path for case-sensitive filesystems
- [ ] Remove duplicate upgrade-arc task from one of the Taskfiles
- [ ] Fix table separator formatting in AGENTS.md line 20

## Summary Stats

- Total issues: 7
- Critical: 0 | High: 3 | Medium: 3 | Low: 1
