# A read-only Orbit report through ToolHive

The daily 10:00 Hermes report used six Orbit calls across three operations on November 25. Configure its model to call the dedicated ToolHive `orbitread` MCP proxy. The proxy filters both tool discovery and calls to `list_tasks`, `get_task` and `list_task_statuses`. Orbit independently checks project membership and `tasks:read` on each call. The shared `unified` gateway serves jobs that need broader access. Its `call_tool` accepts a target name as an argument, so allowing it would give this report a path to other operations. [ToolHive v0.69.1 tool filtering](https://github.com/stacklok/toolhive/blob/v0.69.1/pkg/mcp/tool_filter.go) · [Orbit token permissions](https://github.com/coollabsio/orbit/blob/v0.4.1/docs/mcp.md#api-token-alternative)

## Prepare ToolHive

The [proxy manifests](../kubernetes/apps/ai/toolhive/mcp/orbitread/) are staged separately from the reconciled [ToolHive resource list](../kubernetes/apps/ai/toolhive/mcp/kustomization.yaml). Render them locally with `kubectl kustomize kubernetes/apps/ai/toolhive/mcp/orbitread`. Activation requires a separate approved change.

1. In Orbit **Settings → API tokens**, issue a dedicated service-account token for the report's projects with **Tasks → Read** and an expiry. Record the owner and expiry privately.
2. Store the full `Bearer <token>` header as `ORBIT_READ_MCP_TOKEN` in the existing SOPS-managed `ai/toolhive-secrets`. Only ToolHive injects it on the outbound request to Orbit's HTTPS `/mcp` route. Hermes needs only the internal proxy URL.
3. Once the credential is available, add `orbitread` under `resources` in the ToolHive resource list. After reconciliation, confirm the proxy is Ready and its Service has a ready endpoint. The policy restricts its MCP port to Hermes pods among cluster workloads. Profiles inside the same Hermes pod share its network identity and filesystem.

## Connect Hermes

Back up the owning profile's config and the job's current toolset, schedule, delivery and next run. The existing job is in the default profile and inherited its tools (`null`). Other sessions can inherit MCP servers added to that profile. If their access to the selected projects is unconfirmed, start with a separate profile and local delivery.

Merge this entry under the profile's existing `mcp_servers`, then reload its MCP configuration after approval. The proxy exposes Orbit's original tool names. Use `hermes mcp test orbitread` from that profile to verify the exact three-tool catalog. In a disposable project, verify that pagination reaches the last page and that a read-only identity cannot retrieve a task outside the grant. Use an offline fixture for write-denial checks. [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/website/docs/reference/mcp-config-reference.md)

```yaml
mcp_servers:
  orbitread:
    url: http://mcp-orbitread-remote-proxy.ai.svc.cluster.local:8080/mcp
    trust: untrusted
    tools:
      include: [list_tasks, get_task, list_task_statuses]
      resources: false
      prompts: false
```

## Canary one job

First try a local-delivery, isolated fixture against a disposable Orbit project. Check normal and multi-page reports using `list_tasks` with its project filter and a small `limit`, following `next_cursor` and calling `get_task` only as needed. Compare coverage through the last page with the known fixture results. Missing rows or required data block the cutover. If a genuinely independent file is needed, have a trusted pre-run script emit only its required fields and test the output size. Keep the model's tool set restricted. [Orbit list pagination](https://github.com/coollabsio/orbit/blob/v0.4.1/docs/mcp.md#tools)

The command below targets the existing job in the default profile (`/opt/data`). Use it only if that profile passed the audience check. A trial in a separate profile needs its own local-delivery job and profile home. Leave the original schedule active during that trial. For an approved default-profile cutover, verify the job id, owner and original toolset, coordinate with other editors, and run the command from the checkout containing `./kubeconfig`. The supported scheduler updater acquires the job-store lock. It changes one field on one job and preserves the existing schedule and delivery. Re-read the record after the call. The `no_mcp` marker prevents auto-adding the general MCP endpoint even if the named server disappears. [Scheduler selection](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/cron/scheduler.py#L443) · [Single-job updater](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/cron/jobs.py#L2086)

```bash
read -r -p 'Verified Orbit report job id: ' job_id
kubectl --kubeconfig ./kubeconfig -n ai exec -i deploy/hermes -c app -- \
  /command/s6-setuidgid hermes env HERMES_HOME=/opt/data \
  /opt/hermes/.venv/bin/python -B - "$job_id" <<'PY'
from cron.jobs import get_job, update_job
import sys

job_id = sys.argv[1]
wanted = ["mcp-orbitread", "no_mcp"]
job = get_job(job_id)
if not job or job["id"] != job_id or job.get("enabled_toolsets") is not None:
    raise SystemExit("Stop and review the current job and its toolset")
update_job(job_id, {"enabled_toolsets": wanted})
if get_job(job_id).get("enabled_toolsets") != wanted:
    raise SystemExit("Stored toolset did not match")
PY
```

Observe the next ordinary run. Check execution, delivery outcome, allowed tool names and report completeness without logging task data. To roll back, re-read the job and check its toolset still equals `wanted`, then use the same updater to restore the saved value (`None` for the verified inherited setting). Confirm the restored field. Once no jobs use the proxy, remove the Hermes entry and ToolHive resource reference, revoke the Orbit token and remove its key from the encrypted ToolHive secret. Reverting `enabled_toolsets` to inherited restores the previous broader authority.

## Offline permission checks

Orbit's `task_writes_require_scope_and_versions_and_stay_in_granted_projects` fixture sends a direct write with an initially read-only token and requires an error. It also exercises project scoping in a disposable database. Run from a checkout of the matching release with the frontend asset directory present (an empty `apps/web/dist` is sufficient for this headless fixture).

```bash
cargo test --locked -p orbit-server --test mcp_api \
  task_writes_require_scope_and_versions_and_stay_in_granted_projects -- --exact
```

The normal task read, cursor continuation and token-scope denial passed against an isolated Orbit 0.4.1 fixture. The full MCP suite also exercises a separate cloud-billing guard under a token granted writes. That billing test failed locally, leaving 17 of 18 passing.

In a disposable Hermes 0.23.4 environment, put the YAML example above in `fixture.yaml` and run this from the checkout with its Python environment. The fixture supplies a synthetic server and catalog. It never connects an MCP client or reads production credentials.

```bash
python - <<'PYTEST'
from pathlib import Path
from ruamel.yaml import YAML
from cron.scheduler import _resolve_cron_enabled_toolsets
from tools.mcp_tool_registration import _make_tool_filter

cfg = YAML(typ="safe").load(Path("fixture.yaml").read_text())
entry = cfg["mcp_servers"]["orbitread"]
cfg["mcp_servers"]["toolhive"] = {"enabled": True}
job = {"enabled_toolsets": ["mcp-orbitread", "no_mcp"]}
assert _resolve_cron_enabled_toolsets(job, cfg) == ["mcp-orbitread"]
allowed = _make_tool_filter("orbitread", entry)
catalog = ["list_tasks", "get_task", "list_task_statuses", "get_me", "update_task"]
assert [name for name in catalog if allowed(name)] == catalog[:3]
assert entry["tools"]["resources"] is False and entry["tools"]["prompts"] is False
del cfg["mcp_servers"]["orbitread"]
assert _resolve_cron_enabled_toolsets(job, cfg) == ["mcp-orbitread"]
PYTEST
```

A separate network-disabled test in the digest-pinned Hermes image registered dummy Orbit reads and an unrelated general MCP. The actual deferred dispatcher rejected both the unrelated tool and an unregistered write. After removing the Orbit entry and deregistering its tools, the final registry returned zero callable tools for this job. Before production cutover, verify the live ToolHive catalog, profile access and one complete scheduled report. [Orbit regression](https://github.com/coollabsio/orbit/blob/v0.4.1/apps/server/tests/mcp_api.rs#L676) · [Hermes regression](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/tests/cron/test_cron_mcp_toolset_allowlist.py)
