# Kubernetes Guidance

Applies to all files under `kubernetes/`. Also follow the repository-root `AGENTS.md`.

## Workflow

- Manage cluster state through Flux manifests in this repository; do not edit live resources directly.
- Match nearby applications and components before introducing a new pattern.
- Validate Kubernetes or mixed changes with `bash .agents/skills/pr-review/scripts/validate-pr.sh` before declaring work complete.
- Read only the relevant topic from the [workspace context index](../.agents/learned-workspace.md).

## Conventions

- Use `${SECRET_DOMAIN}` for URLs; never hardcode domains.
- Use YAML anchors only within one `---` document; Kustomize does not resolve anchors across documents.
- Store secrets with SOPS; never commit plaintext secrets or `age.key`.
- Use lowercase-dash Kubernetes names. Applications live under `kubernetes/apps/<namespace>/<app>/`; follow peer layouts and include `ks.yaml`.

Applying or reconciling live resources and decrypting or editing SOPS secrets require user approval.
