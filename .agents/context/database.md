# Database Context

**When to use:** CloudNativePG, CNPG, postgres MCP, database diagnostics, pgbouncer, barman-cloud, or replica recovery.

- CloudNativePG postgres16 and the barman-cloud plugin run in namespace `database`. The plugin Deployment is `barman-cloud-plugin-barman-cloud`; its Service is `barman-cloud`.
- To re-add an instance after its join job was deleted, delete that instance's PVC and force-reconcile so the operator creates a new PVC and join job.
- The `database` MCP group uses `vmcp-database.ai.svc`, port `4483`, path `/mcp`; its public host is `mcp-database.${SECRET_DOMAIN}`.
- It requires `POSTGRES_MCP_DATABASE_URI` in the SOPS-managed `toolhive-secrets`. A read-only target can use `pgbouncer-ro.database.svc.cluster.local:5432`, `dbname=postgres`.
- Use a primary read-write URI for admin statistics, health checks, and tuning, and confirm `pg_is_in_recovery()` is false; a replica skews replication and buffer reporting.
