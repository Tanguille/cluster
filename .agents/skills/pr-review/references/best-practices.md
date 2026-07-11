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

- `flate test all` (or `kustomize build`) succeeds — Helm charts render, not just Kustomization YAML
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
# flate: renders Kustomizations + HelmReleases with the real Helm/Kustomize SDKs (also
# validates YAML syntax/duplicate keys); catches Helm template errors kustomize build can't
# see, since chartRef: OCIRepository is opaque to kustomize
flate test all

# Fallback if flate is unavailable — Kustomization-only, no Helm render
kustomize build kubernetes/apps/<namespace>/<app>/app/

# Flux (offline; without --kustomization-file it queries the cluster API)
flux build ks <name> --path <app>/app --kustomization-file <app>/ks.yaml --dry-run
```
