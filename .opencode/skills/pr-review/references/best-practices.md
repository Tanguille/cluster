# GitOps PR Validation Best Practices

Research current year best practices when reviewing PRs. The landscape evolves quickly.

## Core Validation Layers

A comprehensive GitOps PR validation should cover:

1. **YAML Syntax** - Structural correctness, indentation, formatting
2. **Kubernetes Schema** - Valid API versions, required fields, CRD compliance
3. **Flux/CD Build** - Kustomize builds successfully, no reference errors
4. **HelmRelease** - Chart references valid, values structure correct
5. **Security** - No secrets in plaintext, proper RBAC, network policies
6. **Consistency** - Naming conventions, directory structure, patterns

## Research Prompts

When reviewing PRs, verify against current best practices by researching:

- "2026 GitOps PR validation best practices FluxCD"
- "Kubernetes YAML validation tools 2026"
- "FluxCD kustomization validation patterns"
- "HelmRelease security best practices"

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

## Tools to Consider

Research current validation tooling:

- yamllint - YAML syntax validation
- kubeconform - Kubernetes schema validation
- kustomize - Build and validation
- flux CLI - Build and reconciliation testing
- Local scripts for repository-specific checks

## Repository-Specific Checks

Always cross-reference with:

- `AGENTS.md` - Project conventions and requirements
- `.agents/common-operations.md` - Procedures and patterns
- `.agents/learned-workspace.md` - Workspace-specific knowledge
- Existing app patterns in `kubernetes/apps/`

## Validation Command Pattern

```bash
# Syntax
yamllint -c .yamllint.yaml .

# Schema
kubeconform -strict -ignore-missing-schemas kubernetes/

# Build
kustomize build kubernetes/apps/<namespace>/<app>/

# Flux
flux build kustomization <name> --path <path>
```

## When Uncertain

If validation requirements are unclear:

1. Check existing working examples in the repo
2. Research current best practices online
3. Ask for clarification on project conventions
4. Prefer over-validation to missing issues
