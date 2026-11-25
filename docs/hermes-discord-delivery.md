# Hermes cron delivery failures

The execution ledger and outbox checks apply to every output platform. Use the adapter and route checks below for Discord jobs.

## Read the affected run

Take `profile` and `id` from the alert. For the completion time, use `last_over_time(hermes_cron_last_run_timestamp[30m])` with the same labels, or the last run time in the job record. An alert can remain active for hours after execution. Supply an ISO timestamp with its UTC offset. Run the query from the cluster checkout containing `./kubeconfig`. It reads the matching profile's stores within 30 minutes on either side of completion and prints outcome metadata only.

```bash
read -rp 'Hermes profile: ' profile
read -rp 'Job id: ' job
read -rp 'Completion time with UTC offset: ' instant
kubectl --kubeconfig ./kubeconfig -n ai exec -i deployment/hermes -c app -- \
  /opt/hermes/.venv/bin/python - "$profile" "$job" "$instant" <<'PY'
import json, sqlite3, sys
from pathlib import Path

profile, job, instant = sys.argv[1:]
root = Path("/opt/data")
cron = (root if profile == "default" else root / "profiles" / profile) / "cron"
def db(name):
    return sqlite3.connect((cron / name).as_uri() + "?mode=ro", uri=True)

ledger = db("executions.db")
outbox = db("deliveries.db") if (cron / "deliveries.db").is_file() else None
for execution, started, finished, source, status, outcome in ledger.execute("""
    SELECT id, started_at, finished_at, source, status, delivery_outcome
    FROM executions WHERE job_id = ?
      AND abs(julianday(finished_at) - julianday(?)) <= 0.5 / 24
    ORDER BY julianday(finished_at)""", (job, instant)):
    print("execution", started, finished, source, status, outcome)
    if outbox:
        row = outbox.execute("""
            SELECT created_at, finished_at, status,
                   coalesce(error LIKE '%not configured/enabled%', 0)
            FROM deliveries WHERE execution_id = ?""", (execution,)).fetchone()
        tomb = outbox.execute("SELECT terminal_status FROM delivery_tombstones WHERE execution_id = ?",
                              (execution,)).fetchone() if not row else None
        print("outbox (created, finished, state, config error)", row or tomb or "no row")
state = json.loads((root / "gateway_state.json").read_text())
for key in ("discord", profile + ":discord"):
    entry = state.get("platforms", {}).get(key)
    if entry:
        print("adapter", "primary" if key == "discord" else "profile",
              entry.get("state"), entry.get("updated_at"), entry.get("needs_attention"))
PY
```

A completed execution can have a failed delivery. An outbox row with a creation timestamp proves the worker handed the result to the durable queue. A terminal `failed` row with the configuration flag set points to gateway transport selection. A `delivered` outcome records Hermes's delivery handoff. A `suppressed` outcome records intentional silence. An `unknown` outcome can follow a send with a lost acknowledgement. The current adapter timestamp shows when its state was recorded. It cannot reconstruct the adapter map of an earlier gateway process.

## Discord under multiplexed profiles

For an explicit Discord thread, check the owning profile, parent channel **and thread** against the primary's enabled `gateway.profile_routes` privately. The most-specific matching route must still belong to that profile. A connected primary bot must have View Channel and Send Messages in Threads on the parent and access to the active thread. Keep private destinations, credentials and message contents out of incident reports. Observe the next scheduled run without replaying earlier output.

On November 25, 2026 both affected routes passed the v0.23.4 matcher. Discord confirmed primary-bot membership and effective send permission on both restricted parents' active threads. Both failed outbox rows had already reached their terminal state. The daily job previously delivered on November 23. A sibling in the weekly job's profile delivered to the same destination after the failure. The next natural checks are November 26 at **06:00 CET** (daily) and November 29 at **10:00 CET** (weekly). Use each run's actual completion time in the query.

Change a route only if the owner or exact thread fails the matcher and the intended audience is confirmed. Change a Discord permission only after verifying a missing permission for that bot. If another credential-bearing platform owned by the same profile suppresses the shared Discord adapter, compare the open per-platform fix with the installed version. v0.23.4 already handles a profile with only a local adapter. If the route and permissions pass, capture the primary Discord state and owner-platform state at the next failure. The generic configuration error alone cannot distinguish a route mismatch from an unavailable adapter. [v0.23.4 delivery selection](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/cron/scheduler_delivery.py#L1111-L1165) · [Gateway outbox drain](https://github.com/NousResearch/hermes-agent/blob/v0.23.4/gateway/run.py#L4359-L4380) · [Upstream per-platform fix](https://github.com/NousResearch/hermes-agent/pull/158412) · [Discord permission evaluation](https://docs.discord.com/developers/topics/permissions)
