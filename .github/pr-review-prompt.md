## Cluster-specific review focus

This is a GitOps home-lab cluster on Talos Linux with FluxCD reconciliation. Prioritize:

- **Flux reconciliation**: Kustomization and HelmRelease structure, `ks.yaml` placement under `kubernetes/apps/<app>/`, and whether changes need `dependsOn` or health checks.
- **Talos**: machine config patches follow existing patterns in `talos/`; flag destructive or risky node changes.
- **Secrets**: SOPS-encrypted only; never plaintext credentials or `age.key`.
- **URLs and domains**: use `${SECRET_DOMAIN}`; flag hardcoded domains.
- **MCPServer and ToolHive**: `*-opt` variants duplicate the full `spec` in the same file (no cross-document YAML anchors).
- **Evidence**: shellcheck failures from the evidence provider are blockers when enforcement is enabled.

Defer nitpicks on unrelated files. Prefer root-cause fixes (capacity, config) over silencing alerts or only lowering thresholds.
