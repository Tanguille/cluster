# Talos Guidance

- Read the Talos entries in [learned workspace facts](../.agents/learned-workspace.md) before changing machine configuration.
- Generate configuration with `mise exec -- task talos:generate-config` and validate generated output before declaring work complete.
- Use `mise exec -- task talos:apply-node IP=...` to apply configuration and `mise exec -- task talos:upgrade-node IP=...` to upgrade a node.
- Ask before applying configuration or upgrading a live node.
