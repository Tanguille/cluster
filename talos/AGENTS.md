# Talos Guidance

- Read the Talos entries in [learned workspace facts](../.agents/learned-workspace.md) before changing machine configuration.
- Generate configuration with `mise exec -- just talos generate-config` and validate generated output before declaring work complete.
- Use `mise exec -- just talos apply-node <ip>` to apply configuration and `mise exec -- just talos upgrade-node <ip>` to upgrade a node.
- Ask before applying configuration or upgrading a live node.
