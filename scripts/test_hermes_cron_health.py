"""Offline Hermes cron exporter fixtures. Set PROMTOOL to also evaluate the shipped alert rules."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "kubernetes/apps/ai/hermes/app"
spec = importlib.util.spec_from_file_location("cron_health", APP / "resources/cron_health_export.py")
cron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cron)

# Reuse the id across profiles to exercise Prometheus vector matching.
CASES = {
    "execution": {"last_status": "error", "last_error": "failed"},
    "delivery": {"last_status": "delivery_failed", "last_delivery_error": "failed"},
    "both": {"last_status": "error", "last_error": "failed", "last_delivery_error": "failed"},
    "completed": {"enabled": False, "state": "completed", "last_status": "error",
                  "last_error": "failed", "last_delivery_error": "failed"},
    "queued": {"last_status": "delivery_queued"},
    "cancelled": {"last_status": "cancelled"},
    "never": {"last_status": None, "last_run_at": None},
    "paused": {"last_status": "error", "state": "paused"},
    "disabled": {"last_status": "error", "enabled": False},
    "overdue": {"last_status": "ok", "next_run_at": -7200},
}


def jobs(recovered=False):
    return [(profile, {"id": "shared", "name": "scheduled", "last_run_at": 1} |
             ({"last_status": "ok"} if recovered else fields)) for profile, fields in CASES.items()]


class CronHealthTests(unittest.TestCase):
    def test_statuses(self):
        metrics = dict(line.split() for line in cron.render(jobs(), 100).splitlines())
        def gauge(key, profile):
            return float(metrics[f'hermes_cron_{key}{{profile="{profile}",id="shared",name="scheduled"}}'])
        for profile, execution, delivery in (("execution", 0, 0), ("delivery", 1, 1), ("both", 0, 1),
                                              ("queued", 1, 0), ("never", -1, 0), ("cancelled", -1, 0)):
            with self.subTest(profile=profile):
                self.assertEqual((gauge("last_status_ok", profile), gauge("delivery_failed", profile)),
                                 (execution, delivery))
        self.assertEqual(gauge("last_run_timestamp", "never"), 0)
        self.assertEqual(gauge("overdue_seconds", "overdue"), 7300)
        self.assertEqual(gauge("paused", "paused"), 1)
        self.assertEqual(gauge("enabled", "disabled"), 0)
        self.assertEqual(gauge("enabled", "completed"), 0)
        self.assertEqual(gauge("completed", "completed"), 1)

    def test_stores_and_metric_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            job = {"id": "shared", "name": 'a"b\\c\nd', "last_run_at": "2026-01-01T00:00:00Z"}
            for path, data in ((home / "cron/jobs.json", {"jobs": [job]}),
                               (home / "profiles/team/cron/jobs.json", [job])):
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(data))
            output = cron.render(cron.load_jobs(home), 100)
            for profile in ("default", "team"):
                self.assertIn('hermes_cron_last_run_timestamp{profile="' + profile +
                              r'",id="shared",name="a\"b\\c\nd"} 1767225600', output)
            path.write_text("{")
            with self.assertRaises(json.JSONDecodeError):
                cron.load_jobs(home)

    @unittest.skipUnless(os.getenv("PROMTOOL"), "set PROMTOOL to run the Prometheus rule fixtures")
    def test_alerts(self):
        series = {}
        for minute in (0, 10, 20, 30):
            for line in cron.render(jobs(recovered=minute == 30), minute * 60).splitlines():
                metric, value = line.split()
                series.setdefault(metric, []).append(value)
        checks = []
        for minute, alert, profiles in (
            (20, "JobFailed", ["execution", "both", "completed"]), (20, "DeliveryFailed", ["delivery", "both", "completed"]),
            (20, "Overdue", ["overdue"]), (30, "JobFailed", []), (30, "DeliveryFailed", []),
            (67, "ExporterStale", [None]), (151, "ExporterStale", []), (151, "ExporterAbsent", [None]),
        ):
            checks.append({"expr": f'sum by(profile) (ALERTS{{alertname="HermesCron{alert}",alertstate="firing"}})',
                           "eval_time": f"{minute}m", "exp_samples": [
                               {"labels": f'{{profile="{profile}"}}' if profile else "{}", "value": 1}
                               for profile in profiles]})
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            rules = subprocess.check_output(["yq", "-o=json", ".spec", str(APP / "prometheusrule.yaml")], cwd=ROOT)
            (tmp / "rules.json").write_bytes(rules)
            fixture = {"rule_files": ["rules.json"], "evaluation_interval": "1m", "tests": [{
                "interval": "10m", "input_series": [{"series": key, "values": " ".join(values)}
                                                     for key, values in series.items()],
                "promql_expr_test": checks}]}
            # Continuous exports preserve the last failed outcome. Its warning still ages out.
            weekly = cron.render([("weekly", {"id": "job", "last_status": "error", "last_run_at": 1,
                                              "enabled": False, "state": "completed",
                                              "last_delivery_error": "failed"})], 0).splitlines()
            fixture["tests"].append({
                "interval": "10m", "input_series": [
                    {"series": key, "values": f"{value}+0x40"} for key, value in (line.split() for line in weekly)],
                "promql_expr_test": [
                    {"expr": f'ALERTS{{alertname="HermesCron{kind}Failed",alertstate="firing"}}',
                     "eval_time": age, "exp_samples": []} for kind, age in (("Job", "61m"), ("Delivery", "361m"))]})
            (tmp / "test.json").write_text(json.dumps(fixture))
            subprocess.run([os.environ["PROMTOOL"], "test", "rules", str(tmp / "test.json")], check=True)


if __name__ == "__main__":
    unittest.main()
