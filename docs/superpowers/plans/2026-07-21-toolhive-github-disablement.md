# ToolHive GitHub MCP Disablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Temporarily remove GitHub from ToolHive virtual MCP backends so its repeated stdio initialization failure cannot degrade other gateways.

**Architecture:** Preserve the two GitHub `MCPServer` manifests as YAML comments in their existing configuration file, while retaining the active `MCPToolConfig`. Kustomize then renders no GitHub MCPServer resources; Flux prunes those two workloads and the remaining virtual MCPs no longer discover them.

**Tech Stack:** FluxCD, Kustomize, ToolHive v0.40.1, YAML, flate.

---

### Task 1: Disable the GitHub MCPServer manifests

**Files:**
- Modify: `kubernetes/apps/ai/toolhive/config/github.yaml:1-72`
- Preserve: `kubernetes/apps/ai/toolhive/config/github.yaml:73-79`

- [ ] **Step 1: Verify the current state fails the intended configuration check**

Run:

```bash
grep -c '^kind: MCPServer$' kubernetes/apps/ai/toolhive/config/github.yaml
```

Expected: `2`.

- [ ] **Step 2: Comment out the two MCPServer documents**

Prefix every line in the `github` and `github-opt` documents with `# `. Add
this explanation immediately before them:

```yaml
# Disabled: ToolHive v0.40.1 virtual-MCP health monitoring repeatedly initializes
# GitHub's stdio backend, which returns `duplicate "initialize" received` and
# degrades the resources and unified virtual MCPs. Restore after an upstream fix.
```

Leave `MCPToolConfig/github` active and unchanged.

- [ ] **Step 3: Verify the focused configuration check**

Run:

```bash
grep -c '^kind: MCPServer$' kubernetes/apps/ai/toolhive/config/github.yaml
```

Expected: `0`.

### Task 2: Validate and commit

**Files:**
- Verify: `kubernetes/apps/ai/toolhive/config/github.yaml`

- [ ] **Step 1: Verify the focused diff**

Run:

```bash
git diff --check -- kubernetes/apps/ai/toolhive/config/github.yaml
```

Expected: no whitespace errors.

- [ ] **Step 2: Render with the pinned flate release**

Run:

```bash
mise exec -- flate test all
```

Expected: all Kustomizations and HelmReleases render successfully.

- [ ] **Step 3: Run the repository validation script**

Run:

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Expected: Flux rendering passes; record any pre-existing repository-wide
ShellCheck findings separately from this YAML-only change.

- [ ] **Step 4: Commit and push the approved change**

Run:

```bash
git add kubernetes/apps/ai/toolhive/config/github.yaml
git commit -m "fix(toolhive): disable GitHub MCP backends"
# Run only with the user's explicit push-to-main approval.
git push origin HEAD:main
```
