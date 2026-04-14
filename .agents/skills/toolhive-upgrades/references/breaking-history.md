# ToolHive breaking patterns (rolling log for this repo)

Append a row when a shipped upgrade taught a **repeatable** lesson. Prefer links to upstream release sections.

| Approx version | Symptom / grep signal                                                      | Fix                                                                                                      |
|----------------|----------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| **v0.15**      | `spec.port`, `spec.targetPort`, inline `spec.tools`, plaintext OIDC secret | `proxyPort` / `mcpPort`, `toolConfigRef` + `MCPToolConfig`, `clientSecretRef`, `caBundleRef`             |
| **v0.16**      | Duplicate keys in list fields under SSA                                    | Deduplicate env/volume names; apply CRDs with server-side apply per upstream note                        |
| **v0.16**      | `operator.env` as YAML map in Helm values                                  | Use list of `{name, value}`                                                                              |
| **v0.17**      | Alerts on `.status.phase == "Running"` for ToolHive workloads              | Use **`Ready`**                                                                                          |
| **v0.17+**     | `MCPRegistry` flat `registries[]` only                                     | Migrate to v2 **`sources[]` / `registries[]`** or **`configYAML`** per release                           |
| **v0.19**      | `remoteURL`, `externalURL` in YAML                                         | **`remoteUrl`**, **`externalUrl`** on `MCPServerEntry` / `MCPRemoteProxy`                                |
| **v0.19**      | `enforceServers` on `MCPRegistry`                                          | Field removed — drop it                                                                                  |
| **v0.19**      | `backendAuthType: external_auth_config_ref`                                | Prefer **`externalAuthConfigRef`**                                                                       |
| **v0.20**      | `groupRef: mygroup` bare string                                            | **`groupRef: { name: mygroup }`** on `MCPServer`, `MCPServerEntry`, `VirtualMCPServer`, `MCPRemoteProxy` |
| **v0.20**      | `spec.config.groupRef` on `VirtualMCPServer`                               | Move to **`spec.groupRef.name`** (deprecated path may still resolve)                                     |
| **v0.20**      | Relied on `jwksAllowPrivateIP` alone for protected resource                | Set **`protectedResourceAllowPrivateIP`** too if both must allow private IPs                             |
