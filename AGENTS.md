# AGENTS.md - Agent Coding Guidelines

This file follows the **[AGENTS.md format](https://agents.md)** (plain Markdown, project root) for agent-agnostic context. Tools like OpenCode, Cursor, Copilot, and Aider read it by convention.

## Tool use and context

- **Prefer MCP tools over raw shell** when an MCP tool exists for the task (structured, auditable, less context than long command outputs). Use bash only when no suitable MCP tool exists or for one-off local commands.
- **user-toolhive** exposes **multiple MCP servers** (e.g. flux-operator, Grafana, Home Assistant, others). Check its **available tools** for the task at hand; don’t assume it’s only for one use. For Flux: use its Flux tools to **reconcile** (e.g. `reconcile_flux_source`, `reconcile_flux_resourceset` or equivalent for Kustomizations) and **check state** (`get_flux_instance`, `get_kubernetes_resources` for Kustomization/GitRepository/HelmRelease) instead of only suggesting `task reconcile` or terminal `flux`/`kubectl` commands. For debugging, use `get_kubernetes_logs` to check pod logs.
- **Focused context loading:** Load only the `.agent/` file(s) whose triggers match the current task (see table below). Do not load all `.agent/*.md` upfront—read on demand to keep context lean. Tools that cannot load files on demand (e.g. some `instructions` configs) may list these files to preload; prefer discovery + on-demand read when supported.

### .agent/ (load on demand)

| File                     | Trigger keywords                                                                     | Purpose                                   |
|--------------------------|--------------------------------------------------------------------------------------|-------------------------------------------|
| `learned-preferences.md` | revert, undo, resources, memory, CPU, MCP vs shell                                   | User preferences and tool-choice guidance |
| `learned-workspace.md`   | HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, Talos, Reloader, in-cluster URL | Workspace and CRD facts                   |
| `common-operations.md`   | add app, new application, upgrade, SOPS, secrets, encrypt                            | Procedures: add app, upgrade, secrets     |

Load a file only when the task matches its triggers. Single source of truth—update here for continual-learning.

## Overview

This is a **GitOps-based Kubernetes cluster repository** running on Talos Linux. It uses FluxCD for GitOps reconciliation. Most changes should be made through this repository rather than directly to the cluster.

## Project Structure

- `kubernetes/` - Kubernetes manifests (FluxCD Kustomizations, HelmReleases)
- `talos/` - Talos Linux machine configs
- `bootstrap/` - Bootstrap Helmfile configurations
- `.taskfiles/` - Task definitions for automation
- `.github/workflows/` - CI/CD workflows
- **`docs/`** - Human-facing docs: **`useful_commands.md`** (Flux, kubectl, Talos), **`common-operations.md`** (add app, upgrade, secrets)
- **`.agent/`** - Extra agent context (convention; not part of the [agents.md](https://agents.md) spec). Load on demand using the **.agent/ (load on demand)** table above; do not load all files upfront. OpenCode: leave `instructions` empty for on-demand loading, or list files to preload; other tools: read file when triggers match.

## Build/Lint/Test Commands

### Task Runner (Primary)

```bash
task                           # List all available tasks
task reconcile                 # Force Flux to pull changes from Git
```

### Kubernetes Tasks

```bash
task kubernetes:encrypt        # Encrypt all Kubernetes SOPS secrets
task kubernetes:resources      # Gather cluster resources for support
```

### Talos Tasks

```bash
task talos:generate-config     # Generate Talos configuration
task talos:apply-node IP=...   # Apply Talos config to a node
task talos:upgrade-node IP=... # Upgrade Talos on a single node
task talos:upgrade-k8s         # Upgrade Kubernetes
```

### Linting/Validation

**YAML Formatting** (yamlfmt):

```bash
yamlfmt -w kubernetes/          # Format YAML files (excludes *.sops.yaml)
```

**Kubernetes Validation** (prefer scoped path for speed when only a subset changed):

```bash
kubeconform -strict -original-location kubernetes/
# or: kubeconform -strict -original-location kubernetes/apps/<app>/
```

**Shell Scripts** (shellcheck):

```bash
shellcheck scripts/*.sh
```

**Flux Local Testing** (for PRs):

```bash
flux-local test --all-namespaces --enable-helm --path kubernetes/flux/cluster
flux-local diff helmrelease --path kubernetes/flux/cluster
```

### Environment Setup

This project uses **mise** for tool management:

```bash
mise install           # Install all tools defined in .mise.toml
mise exec -- helm ...  # Run tools without global installation
```

Required tools: flux, helm, kubectl, kustomize, sops, age, talhelper, talosctl, yq, jq, kubeconform, yamlfmt

## Code Style Guidelines

### URLs and Domains

- **Never hardcode domains** - Always use `${SECRET_DOMAIN}` variable for URLs
- The `${SECRET_DOMAIN}` variable is defined in `cluster-secrets.sops.yaml` and substituted by Flux
- Example: `https://grafana.${SECRET_DOMAIN}` instead of `https://grafana.domain.tld`

### YAML Files

- **Indent:** 2 spaces (NOT tabs)
- **Line endings:** LF (Unix)
- **Trailing whitespace:** Trimmed
- **Final newline:** Required
- **Document start:** `---` at start of YAML files
- **Formatting:** Use `yamlfmt` to auto-format
- **DRY:** Use YAML anchors (`&name` / `*name`) to deduplicate repeated blocks (e.g. probe `httpGet`, resource `requests`) instead of copying the same values

### Secrets (SOPS)

- **Never commit plaintext secrets**
- Use `sops` for encrypted secrets (Age encryption)
- Encrypted files use `.sops.yaml` or `.sops.json` extension
- Key file: `age.key` (do not commit)
- Edit encrypted files with: `sops <file>`

### Kubernetes Resources

- Naming: lowercase with dashes; HelmReleases `<app>-<namespace>`; labels `app.kubernetes.io/name` / `instance`
- Place resources in `kubernetes/apps/<app>/<type>/`; include `ks.yaml` for Flux Kustomizations

### GitOps Principles

1. **All changes via Git** - Never modify cluster resources directly
2. **Declarative manifests** - Define desired state, not imperative steps
3. **Drift detection** - Flux will detect and revert manual changes
4. **Reconciliation** - Use `task reconcile` to trigger Flux sync

### Shell Scripts

- Use `#!/usr/bin/env bash` shebang
- 4-space indentation
- Use `set -euo pipefail`
- Check: `shellcheck` before committing

### Documentation

- Markdown: 4-space indent; manual procedures in `docs/`

## Error Handling

- **Preconditions:** Use Taskfile preconditions to verify prerequisites
- **Secrets:** Fail early if SOPS keys missing (`test -f age.key`)
- **Validation:** Run kubeconform before committing K8s changes

## Common Operations

See **`docs/common-operations.md`** for: adding an app, upgrading apps, secrets (SOPS).

## Testing Changes

For any K8s changes:

1. Validate: `kubeconform -strict kubernetes/`
2. Format: `yamlfmt -w kubernetes/`
3. Test locally (if flux-local available):

   ```bash
   flux-local test --all-namespaces --enable-helm --path kubernetes/flux/cluster
   ```

## Communication

- **Keep agents concise** - If a response would be too long, reference other sources (docs, code, previous context) instead of repeating
- **Always ask first** - Never push anything without explicit user confirmation
- **Multi-line fixes = PR** - If a fix changes more than one line, create a pull request instead of direct commits

## Safety and permissions

- **Allowed without prompt:** Read files, list dirs, run validation (kubeconform, yamlfmt, shellcheck), run flux-local test/diff, format/lint.
- **Ask first:** Git push (including force push, unless the user explicitly asked for a rebase/merge), applying to the live cluster (e.g. `task reconcile`, `flux reconcile`, `talos apply`), decrypting or editing SOPS secrets, deleting resources.

## When stuck

- Ask a clarifying question, propose a short plan, or open a draft PR with notes. Do not push large speculative changes without confirmation.

## PR / commit checklist

- **Title:** Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) (e.g. `feat(observability): add speedtest exporter`, `fix(toolhive): correct MCP transport`). Type + optional scope + short description.
- **Before commit:** Lint/validate green (see **Linting/Validation** and **Testing Changes** above for commands); diff small and focused.
- **Never:** Commit plaintext secrets, age.key, or unencrypted SOPS files. Do not force push unless the user explicitly asked for a rebase or merge situation.

## Important Notes

- Infrastructure-as-code; review diffs before committing; use flux-local in PRs; never commit secrets or age.key.

## Learned preferences and workspace facts

User preferences and workspace facts live in **`.agent/learned-preferences.md`** and **`.agent/learned-workspace.md`** (continual-learning or manual updates go there). Load on demand using the **.agent/ (load on demand)** table above when the task matches the file’s trigger keywords.
