# A read-only Orbit report in Hermes

The daily 10:00 Orbit report explicitly prohibits changes to tasks and comments. Its November 25 run made six Orbit calls across three operations. Two local code/shell calls processed JSON spill files. The model currently inherits its profile's tools. This cutover gives Orbit responsibility for denying writes and limits the report's model to the three reads it used. Versions verified were Orbit 0.4.1 and Hermes 0.23.4.

## Prepare the credential and configuration

1. In Orbit **Settings → API tokens**, issue a dedicated service-account token with **Tasks → Read**, only the report's project(s), and an expiry. Keep docs, chat, export and write scopes off. Verify the grant in Orbit. Record its expiry and owner privately. Leave the operator's existing ToolHive credential and endpoint alone. [Orbit token setup and permissions](https://github.com/coollabsio/orbit/blob/v0.4.1/docs/mcp.md#api-token-alternative)
2. Record a private backup of the owning profile's config and the target job's current `enabled_toolsets`, schedule, delivery and next run. The original job inherited its tools (`null`). Provide the raw token to **that profile only** as `ORBIT_READ_TOKEN`, using its secret source or protected `.env`. Keep credential values out of commands, Git and job records. The existing profiles share a Unix identity and data volume. This limits what the scheduled model can invoke. A separate workload, credential mount and storage are needed for filesystem isolation.
3. In the owning profile's `config.yaml`, merge this entry under its existing `mcp_servers`. Replace the example URL with the verified Orbit HTTPS endpoint and retain TLS verification. Profile-level discovery also exposes a new endpoint to other interactive sessions and jobs that inherit MCP servers. Even a cron job selecting only a native toolset such as `web` can pick up that server automatically. Check whether those consumers already have access to the selected projects. If their access cannot be confirmed, use a separately scoped profile with local delivery for the trial. [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/website/docs/reference/mcp-config-reference.md)

```yaml
mcp_servers:
  orbitread:
    url: https://orbit.example.invalid/mcp
    headers:
      Authorization: "Bearer ${ORBIT_READ_TOKEN}"
    trust: untrusted
    tools:
      include: [list_tasks, get_task, list_task_statuses]
      resources: false
      prompts: false
```

With approval, reload the owning runtime's MCP configuration and verify the gateway sees `orbitread` in that profile. Use `hermes mcp test orbitread` there to inspect the endpoint's answer. Orbit's native catalog also exposes other reads in the granted projects. Separately inspect the **model's effective tool list** with the per-job selection below. The underlying callable tools must be exactly the three Orbit reads. Hermes can present native `tool_search`, `tool_describe` and `tool_call` wrappers when tools are deferred. Verify they cannot dispatch the general ToolHive endpoint, shell, code execution, files or delegation. Orbit marks its read tools with the read-only hint. [Hermes MCP test](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/website/docs/user-guide/features/mcp.md) · [Orbit authorization at listing and dispatch](https://github.com/coollabsio/orbit/blob/v0.4.1/apps/server/src/mcp.rs#L608)

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

Observe the next ordinary run. Check execution, delivery outcome, allowed tool names and report completeness without logging task data. To roll back, re-read the job and check its toolset still equals `wanted`, then use the same updater to restore the saved value (`None` for the verified inherited setting). Confirm the restored field, remove the unused named entry and revoke its dedicated token after all users have transitioned. Reverting `enabled_toolsets` to inherited restores the previous broader authority.

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

A separate network-disabled test in the digest-pinned Hermes image registered dummy Orbit reads and an unrelated general MCP. The actual deferred dispatcher rejected both the unrelated tool and an unregistered write. After removing the Orbit entry and deregistering its tools, the final registry returned zero callable tools for this job. Production still needs the dedicated token, the audience decision and a successful scheduled report. [Orbit regression](https://github.com/coollabsio/orbit/blob/v0.4.1/apps/server/tests/mcp_api.rs#L676) · [Hermes regression](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/tests/cron/test_cron_mcp_toolset_allowlist.py)
