# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, cluster-secrets, app-template, kopiur, openebs-hostpath, Talos, Reloader, in-cluster URL, CNPG, Rook, or continual learning.

Stable, non-sensitive facts about this cluster and tooling.

- For continual learning, update `.agents/learned-preferences.md` or this file; keep full detail in `.agents/` rather than `AGENTS.md`.
- Use in-cluster service URLs (e.g. `http://service.namespace.svc.cluster.local:port`) for pod-to-pod calls to avoid external DNS or hairpinning.
- Internal HTTPRoutes use parentRef name `envoy-internal`; k8s-gateway serves DNS for routes attached to that gateway.

## ToolHive / MCP

- MCPServer secret-backed env vars use `spec.secrets` with `targetEnvName`; `env[].valueFrom` is unsupported. Transport values are `streamable-http` (e.g. `talos-mcp`) or `stdio` (e.g. `ghostfolio`) — never `streamablehttp`.
- VMCP session storage uses Redis at `dragonfly.database.svc.cluster.local:6379`.

### GitHub (`github_*`)

All agents route GitHub work here when the tools are available, via `call_tool` with
`tool_name = github_<name>` (prefix mandatory) and backend params inside `parameters`. The Hermes
box has no `gh`/`GITHUB_TOKEN`, so there it is the only route.

| Job | Tool |
|---|---|
| Commit one file to a branch (FULL contents, 1 commit/call) | `github_create_or_update_file` — `path`, `content`, `message`, blob `sha` when updating |
| Delete a file | `github_delete_file` |
| New branch | `github_create_branch` |

- Read current contents before pushing (worktree, or `get_diff`/`get_files`, or raw.githubusercontent.com).
- `create_or_update_file` bypasses local hooks; for code changes verify in a worktree first.
- Degraded envelope: check `agent.log` first ("unknown argument" = cron model-routing bug, not
  outage), then `~/.hermes/scripts/toolhive_retry.py call <tool> <json>` — flags BEFORE positionals;
  its SDK client crashes on large payloads even when the server succeeds, trust the raw HTTP
  fallback it prints.
- CI triage: `github_actions_list` (`list_workflow_runs`/`list_workflow_jobs`), `github_actions_get`
  (`get_workflow_run`), `github_get_job_logs` (`run_id` + `failed_only` + `return_content`),
  `github_actions_run_trigger` (`rerun_failed_jobs`); the `actions` toolset comes from the entry's
  `X-MCP-Toolsets` header. If `find_tool` lacks them, ssh-gh fallbacks (server = fish, NO heredocs; one-liners only):
  `gh run view <id> --repo Tanguille/cluster --log-failed` · `gh run rerun <id> --repo Tanguille/cluster --failed` · `gh run list --branch main --limit 1`.
- Last resort (conflict resolution only): worktree commit → `git bundle` → `scp` to server → push
  from `~/cluster` (authed gh).

## Talos

- Talos kernel arguments belong in `schematic.yaml`; existing nodes only get them through an upgrade to the new schematic's Factory installer.
- Large Talos `/var/lib/containerd` usage is often old image layers. Kubelet image GC thresholds are already tuned in `talos/cluster.yaml.j2`; confirm Talos apply mode before assuming a reboot is unnecessary.

## Storage

- With `ceph-block` RWO storage, use Deployment strategy `Recreate`; RollingUpdate is unsupported.
- The app-template chart defaults to `Recreate`. If it emits `rollingUpdate` too, fix the chart, postRenderer, or patch.
- `openebs-hostpath` does not enforce PVC size; the node disk is the only limit, so a volume holding more than its request is not an overflow.
- The shared kopiur SnapshotPolicy's `sourcePathOverride: /data` is the path recorded in the snapshot identity, not the app's mount path: the mover mounts the PVC itself, so apps mounting it elsewhere (`/app/data`, `/config`) are backed up.
- CSI `readAffinity` is off: with it, every CephFS kernel write fails silently on Ceph v21 (files read back as zeros). Re-enable only on a kernel with libceph `d6c7a5fcd088`; the condition is in the rook-ceph cluster HelmRelease.

## Flux

- Flux `postBuild.substituteFrom` replaces `${...}` in rendered manifests. Escape literals with Flux/Kustomize `$$` patterns, or Flux may empty unintended matches.
- The cluster-apps patch (`kubernetes/flux/cluster/ks.yaml`) gives every child Kustomization `substituteFrom` cluster-settings and cluster-secrets, so `${SECRET_DOMAIN}` resolves in all child output, `configMapGenerator` data included.

## app-template

- A workload is named after the release alone when it has one controller (`opencode`, not `opencode-app`); the controller name is appended only with several controllers.
- `automountServiceAccountToken` defaults to `false`; an app that omits it mounts no token.
- A route without `rules` gets its backend from the service matching the route identifier.
- Chart-made `configMaps` roll the pod through a `checksum/configMaps` annotation over their `data` (not `binaryData`); Reloader is only for ConfigMaps and Secrets made outside the chart.
- Images are pinned as `tag: <version>@sha256:<digest>` in every chart, also where the chart has a `digest` field, so Renovate bumps both in one value.

## CNPG

- CNPG ignores `priorityClassName` in its pod diff; roll it out with the `kubectl.kubernetes.io/restartedAt` annotation on the Cluster. Flux strips a kubectl-set annotation within minutes; the suspend/annotate/resume sequence is in `docs/useful_commands.md` § PostgreSQL (CNPG).
- CloudNativePG postgres16 and the barman-cloud plugin run in namespace `database`. The plugin Deployment is `barman-cloud-plugin-barman-cloud`; its Service is `barman-cloud`. To re-add an instance after its join job was deleted, delete its PVC and force-reconcile.

## Operations

- Annotate relevant deployments with Reloader (e.g. `reloader.stakater.com/auto: "true"`) so pods restart when referenced ConfigMaps or Secrets change.
- Memory requests sit just below the 7-day average and round down (64Mi steps below 1Gi), so a request under the average its comment cites is intended.
- For one-shot privileged pods, use a YAML manifest and `kubectl apply -f`; complex `kubectl run --overrides` JSON is unreliable. Delete an existing pod before reapplying because pod specs are largely immutable.
- Follow KISS principles; avoid init containers or extra complexity unless needed.
- Drain safety: PDB, DaemonSet, node-pinned and drain-role pods run at `homelab-critical` (why: `kube-system/priorityclass.yaml`); the `DaemonSetPodWithoutPriorityClass` alert catches unclassed DaemonSets.
