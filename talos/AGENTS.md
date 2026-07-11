# Talos Guidance

Applies to all files under `talos/`. Also follow the repository-root `AGENTS.md`.

- Read [Talos context](../.agents/context/talos.md) before changing machine configuration.
- Generate configuration with `mise exec -- task talos:generate-config` and validate generated output before declaring work complete.
- Use `mise exec -- task talos:apply-node IP=...` to apply configuration and `mise exec -- task talos:upgrade-node IP=...` to upgrade a node.
- Applying configuration or upgrading a live node requires user approval.
