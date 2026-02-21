# AGENTS.md - Agent Coding Guidelines

## Overview

This is a **GitOps-based Kubernetes cluster repository** running on Talos Linux. It uses FluxCD for GitOps reconciliation. Most changes should be made through this repository rather than directly to the cluster.

## Project Structure

- `kubernetes/` - Kubernetes manifests (FluxCD Kustomizations, HelmReleases)
- `talos/` - Talos Linux machine configs
- `bootstrap/` - Bootstrap Helmfile configurations
- `.taskfiles/` - Task definitions for automation
- `.github/workflows/` - CI/CD workflows

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

**Kubernetes Validation**:
```bash
kubeconform -strict -original-location kubernetes/
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

### YAML Files
- **Indent:** 2 spaces (NOT tabs)
- **Line endings:** LF (Unix)
- **Trailing whitespace:** Trimmed
- **Final newline:** Required
- **Document start:** `---` at start of YAML files
- **Formatting:** Use `yamlfmt` to auto-format

### Secrets (SOPS)
- **Never commit plaintext secrets**
- Use `sops` for encrypted secrets (Age encryption)
- Encrypted files use `.sops.yaml` or `.sops.json` extension
- Key file: `age.key` (do not commit)
- Edit encrypted files with: `sops <file>`

### Kubernetes Resources

**Naming Conventions:**
- Use lowercase with dashes: `my-resource-name`
- HelmReleases: `<app>-<namespace>` pattern
- Namespaces: lowercase, descriptive
- Labels: consistent `app.kubernetes.io/name`, `app.kubernetes.io/instance`

**Resource Structure:**
```yaml
apiVersion: <group>/<version>
kind: <ResourceType>
metadata:
  name: <resource-name>
  namespace: <namespace>
spec:
  # Resource-specific configuration
```

**Flux Integration:**
- Place resources in `kubernetes/apps/<app>/<type>/`
- Use Kustomization overlays for environment differences
- Include `ks.yaml` for Flux Kustomization definitions

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
- Markdown files: 4-space indent
- Keep README.md updated for major changes
- Document manual procedures in `docs/`

## Error Handling

- **Preconditions:** Use Taskfile preconditions to verify prerequisites
- **Secrets:** Fail early if SOPS keys missing (`test -f age.key`)
- **Validation:** Run kubeconform before committing K8s changes

## Common Operations

### Adding New Application
1. Create namespace in `kubernetes/components/common/`
2. Add HelmRepository if external
3. Create app in `kubernetes/apps/<app>/`
4. Add Kustomization in appropriate `ks.yaml`
5. Run validation: `kubeconform -strict kubernetes/`

### Upgrading Applications
1. Update `image.tag` in HelmRelease or kustomization.yaml
2. Commit and push changes
3. Flux will auto-reconcile (or run `task reconcile`)

### Secrets Management
1. Create unencrypted file first
2. Encrypt with: `sops --encrypt --in-place <file>`
3. Or create with: `sops <file>.yaml` (edits encrypted)

## Testing Changes

For any K8s changes:
1. Validate: `kubeconform -strict kubernetes/`
2. Format: `yamlfmt -w kubernetes/`
3. Test locally (if flux-local available):
   ```bash
   flux-local test --all-namespaces --path kubernetes/flux/cluster
   ```

## Important Notes

- This is infrastructure-as-code; changes affect production
- Always review diffs before committing
- Use flux-local in PRs to catch issues early
- Never commit secrets or the age.key file
