# Kubernetes Guidance

- Manage cluster state through Flux manifests in this repository; do not edit live resources directly.
- Match nearby applications and components before introducing a new pattern.
- Validate Kubernetes or mixed changes with `bash .agents/skills/pr-review/scripts/validate-pr.sh` before declaring work complete.
- Read [learned workspace facts](../.agents/learned-workspace.md) only when its trigger keywords match the task.
- Use `${SECRET_DOMAIN}` for URLs; never hardcode domains. Chart source URLs (`HelmRepository`, `OCIRepository`) stay as their publishers publish them.
- Use YAML anchors only within one `---` document; Kustomize does not resolve anchors across documents.
- Store secrets with SOPS; never commit plaintext secrets or `age.key`.
- Use lowercase-dash Kubernetes names. Applications live under `kubernetes/apps/<namespace>/<app>/`; follow peer layouts and include `ks.yaml`.
- Check new or changed selectors and label references (NetworkPolicy peers, affinity, ServiceMonitor ports, alert annotations) against the labels and ports the target actually has, rendered or live.
- Ask before applying or reconciling live resources or decrypting or editing SOPS secrets.
