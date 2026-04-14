# ToolHive upgrade workflow (reference)

## Compare two versions

- Full diff: `https://github.com/stacklok/toolhive/compare/vOLD...vNEW`
- Release page: `https://github.com/stacklok/toolhive/releases/tag/vNEW`

Read **Breaking Changes** first, then **Deprecations**, then **Improvements** (optional features).

## GitOps ordering

1. Flux: bump **`toolhive-operator-crds`** OCI tag (or equivalent HelmRelease) and reconcile until healthy.
2. Bump **`toolhive-operator`** chart tag; reconcile.

If admission rejects old manifests, CRDs were likely applied too late—roll back operator pin, fix YAML, re-apply CRDs, then operator.

## Backup (optional, high-stakes clusters)

```bash
kubectl get mcpservers,virtualmcpservers,mcpgroups,mcpserverentries,mcpremoteproxies,mcpregistries -A -o yaml > /tmp/toolhive-cr-backup.yaml
```

## Post-upgrade smoke

```bash
kubectl get helmrelease -n <flux-ns> | rg -i toolhive
kubectl get pods -n ai -l 'app.kubernetes.io/name' -o wide 2>/dev/null || kubectl get pods -n ai
```

Confirm `VirtualMCPServer` / `MCPServer` phases use **`Ready`** (not legacy **`Running`**) where the release notes standardized phases.

## Registry (MCPRegistry) upgrades

v0.17+ moved registry spec toward **v2** (`sources[]` / `registries[]`) and later toward **`configYAML`**. If this repo adds `MCPRegistry`, read that version’s migration block literally—registry mistakes fail open in confusing ways.

## Helm values

Watch for **value type** changes (e.g. `operator.env` map → list of `{name,value}`). Grep `values.yaml` in upstream chart for the target tag when upgrading.

## Subagent review gate

Before marking the upgrade **done**, spawn **code-reviewer** using the copy-paste template in [reviewer-handoff.md](reviewer-handoff.md). Fix blocking items (or get explicit user acceptance of risk), then re-run the reviewer once if needed.
