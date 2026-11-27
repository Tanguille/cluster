"""Push all Hermes profiles' latest cron outcomes to VictoriaMetrics from one script-only job.

jobs.json is a current-state store. Deleted jobs and outcomes overwritten between polls have no sample.
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


def load_jobs(home):
    stores = [("default", home / "cron/jobs.json")]
    stores += [(path.parents[1].name, path) for path in sorted((home / "profiles").glob("*/cron/jobs.json"))]
    # A broken store must fail the export before the heartbeat can claim it succeeded.
    jobs = []
    for profile, path in stores:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        data = json.loads(raw)
        jobs += [(profile, job) for job in (data["jobs"] if isinstance(data, dict) else data)]
    return jobs


def timestamp(value):
    if not value:
        return 0
    if isinstance(value, (float, int)):
        return value
    return datetime.fromisoformat(value).timestamp()


def escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(jobs, now):
    lines = [f"hermes_cron_jobs_total {len(jobs)}", f"hermes_cron_export_timestamp {now}"]
    for profile, job in jobs:
        labels = {"profile": profile, "id": job["id"], "name": job.get("name") or job["id"]}
        series = ",".join(f'{key}="{escape(value)}"' for key, value in labels.items())
        last_run = timestamp(job.get("last_run_at"))
        next_run = timestamp(job.get("next_run_at"))
        status = job.get("last_status")
        error = job.get("last_error") or ""
        delivery_error = (job.get("last_delivery_error") or "").strip()
        enabled = job.get("enabled", True)
        paused = job.get("state") == "paused" or bool(job.get("paused_at"))

        # -1 is unknown or cancelled. delivery_queued and delivery_failed both follow successful execution.
        execution_ok = -1
        if last_run and status != "cancelled":
            if error or status in ("error", "failed"):
                execution_ok = 0
            elif status in ("ok", "success", "delivery_failed", "delivery_queued"):
                execution_ok = 1

        metrics = {
            "last_run_timestamp": last_run,
            "next_run_timestamp": next_run,
            "last_status_ok": execution_ok,
            "delivery_failed": int(bool(last_run and (status == "delivery_failed" or delivery_error))),
            "enabled": int(enabled),
            "completed": int(job.get("state") == "completed"),
            "paused": int(paused),
            "overdue_seconds": max(0, now - next_run) if next_run and enabled and not paused else 0,
            "last_error_backend_unavailable": int("no backends available" in error.lower()),
        }
        lines.extend(f"hermes_cron_{metric}{{{series}}} {value}" for metric, value in metrics.items())
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", help="render the current stores without pushing")
    args = parser.parse_args()
    body = render(load_jobs(Path(os.environ.get("HERMES_HOME", "/opt/data"))), int(time.time()))
    if args.print:
        print(body, end="")
        return
    base = os.environ.get("VM_URL", "http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428")
    request = Request(base.rstrip("/") + "/api/v1/import/prometheus", data=body.encode(),
                      headers={"Content-Type": "text/plain; charset=utf-8"})
    with urlopen(request, timeout=15) as response:
        response.read()


if __name__ == "__main__":
    main()
