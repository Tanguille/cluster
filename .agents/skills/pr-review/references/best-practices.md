# GitOps PR Validation Best Practices

## Key Areas to Validate

### YAML Quality

- Indentation consistency (typically 2 spaces)
- Line length limits
- No trailing whitespace
- Document separators where appropriate

### Schema Compliance

- Valid Kubernetes API versions
- Flux CRD compliance (Kustomization, HelmRelease, GitRepository, etc.)
- Custom resource definitions match cluster CRDs

### Build Validation

- Kustomize builds without errors
- All referenced files exist
- No circular dependencies

### Security

- SOPS encryption on secrets
- No hardcoded credentials
- Proper RBAC configurations
- Network policies defined

### Repository Conventions

- Follow project-specific patterns in AGENTS.md
- Consistent naming (lowercase-dashes)
- Proper directory structure
- DRY principles with kustomize patches/transformers

## Repository-Specific Checks

Always cross-reference with:

- `AGENTS.md` - Project conventions and requirements
- `.agents/common-operations.md` - Procedures and patterns
- `.agents/learned-workspace.md` - Workspace-specific knowledge
- Existing app patterns in `kubernetes/apps/`

## Validation Command Pattern

```bash
# Build (also validates YAML syntax and duplicate keys)
kustomize build kubernetes/apps/<namespace>/<app>/app/

# Flux (offline; without --kustomization-file it queries the cluster API)
flux build ks <name> --path <app>/app --kustomization-file <app>/ks.yaml --dry-run
```
