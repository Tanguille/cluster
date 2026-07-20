# ToolHive GitHub MCP temporary disablement

## Goal

Restore the health of the `resources` and `unified` virtual MCPs by removing
the GitHub MCP backends that repeatedly fail ToolHive's backend health checks.

## Change

Comment out the `github` and `github-opt` `MCPServer` documents in
`kubernetes/apps/ai/toolhive/config/github.yaml`. Retain the definitions and
the `MCPToolConfig`, with a concise comment that records the ToolHive v0.40.1
stdio fan-in failure: repeated `initialize` calls receive `duplicate
"initialize" received`.

## Scope and recovery

No other MCPServer, MCPGroup, VirtualMCPServer, secret, or route changes.
Flux removes the two GitHub MCPServer workloads; the remaining virtual MCPs no
longer include them. Restoring GitHub later consists of uncommenting the two
documents after an upstream-compatible solution is available.

## Verification

Render the repository with the pinned `flate` version, run the repository
validation script, check the focused diff, then reconcile only with explicit
approval and confirm `resources` and `unified` readiness.
