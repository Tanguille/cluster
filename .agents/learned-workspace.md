# Learned Workspace Context

**When to use:** cluster-specific behavior, HTTPRoute, ToolHive, MCPServer, Flux, Talos, storage, database, Moltis, media, or continual learning.

Load only the topic relevant to the task:

- [Kubernetes and Flux](context/kubernetes.md): routing, Reloader, in-cluster URLs, one-shot pods, substitutions, and workload patterns
- [ToolHive and Moltis](context/toolhive.md): MCPServer rules, endpoints, optimizer gateway, observability, and Moltis integration
- [Database](context/database.md): CloudNativePG recovery and postgres MCP connectivity
- [Storage](context/storage.md): Ceph block strategy and monitor disk pressure
- [Talos](context/talos.md): schematics, kernel arguments, kubelet image garbage collection, and operational commands
- [Media](context/media.md): Recyclarr behavior and configuration

For continual learning, add stable facts to the narrowest topic file. Add or update this index only when routing changes.
