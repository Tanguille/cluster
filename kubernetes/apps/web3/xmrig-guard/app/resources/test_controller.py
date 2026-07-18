import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(__file__))
import controller

UTC = timezone.utc
ROOT = os.path.dirname(__file__)


def config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as stream:
        return controller.Config.load(json.load(stream))


class ConfigTests(unittest.TestCase):
    def test_complete_policy_configuration(self):
        cfg = config()
        self.assertEqual(set(cfg.nodes), {"control-1", "control-2", "control-3"})
        self.assertEqual(cfg.policies["control-1"], {"kind": "cpu"})
        self.assertEqual(len(cfg.sensors["control-2"]), 7)
        self.assertEqual(len(cfg.sensors["control-3"]), 8)

    def test_missing_or_changed_policy_is_rejected(self):
        values = {"mode": "observe"}
        with self.assertRaises(ValueError):
            controller.Config.load(values)
        values = json.loads(json.dumps(config_values()))
        values["policies"]["control-2"]["sensors"] = []
        with self.assertRaises(ValueError):
            controller.Config.load(values)

    def test_unknown_node_and_wrong_kind_are_rejected(self):
        values = config_values()
        values["policies"]["control-4"] = {"kind": "nvme", "sensors": []}
        with self.assertRaises(ValueError):
            controller.Config.load(values)

    def test_policy_keys_are_exact_for_each_kind(self):
        values = config_values()
        values["policies"]["control-1"]["sensors"] = [{"chip": "unexpected", "sensor": "temp1"}]
        with self.assertRaises(ValueError):
            controller.Config.load(values)
        values = config_values()
        values["policies"]["control-2"]["extra"] = False
        with self.assertRaises(ValueError):
            controller.Config.load(values)
        values = config_values()
        values["policies"]["control-1"]["kind"] = "nvme"
        values["policies"]["control-1"]["sensors"] = []
        with self.assertRaises(ValueError):
            controller.Config.load(values)

    def test_threshold_and_timing_changes_are_rejected(self):
        values = config_values()
        values["thresholds"]["cpu"]["trip"] = 71
        with self.assertRaises(ValueError):
            controller.Config.load(values)
        values = config_values()
        values["timing"]["tripDwellSeconds"] = 121
        with self.assertRaises(ValueError):
            controller.Config.load(values)


def config_values():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as stream:
        return json.load(stream)


class PolicyTests(unittest.TestCase):
    def test_monotonic_dwell_and_duplicate_source(self):
        p = controller.DwellPolicy(60, 70, 10, 2, 60)
        source = datetime(2026, 1, 1, tzinfo=UTC)
        self.assertFalse(p.observe(59, source, 100.0))
        self.assertFalse(p.observe(59, source, 1000.0))
        self.assertTrue(p.observe(59, source + timedelta(seconds=1), 110.0))

    def test_invalid_and_gap_reset_safe_state(self):
        p = controller.DwellPolicy(60, 70, 1, 1, 2)
        source = datetime(2026, 1, 1, tzinfo=UTC)
        p.observe(59, source, 0)
        p.observe(59, source + timedelta(seconds=1), 1)
        self.assertTrue(p.safe)
        self.assertFalse(p.observe(float("nan"), source + timedelta(seconds=2), 2))
        self.assertFalse(p.safe)
        p.observe(59, source + timedelta(seconds=10), 3)
        self.assertFalse(p.safe)


class TelemetryTests(unittest.TestCase):
    def test_nvme_requires_exact_set_and_fixed_evaluation_time(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        metric = {"kubernetes_node": "control-2", "chip": "nvme_nvme0", "sensor": "temp1"}
        transport.get.return_value = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": metric, "value": [str(now.timestamp()), "42"]}]}}
        client = controller.VictoriaMetricsClient("http://vm", transport)
        self.assertEqual(client.query_nvme("control-2", [("nvme_nvme0", "temp1")], now)[0].value, 42)
        self.assertEqual(transport.get.call_args.args[1]["time"], now.isoformat().replace("+00:00", "Z"))
        with self.assertRaises(ValueError):
            client.query_nvme("control-2", [("nvme_nvme0", "temp1"), ("nvme_nvme0", "temp2")], now)

    def test_cpu_query_contains_label_join_and_separate_sources(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "37"]}]}}
        transport.get.return_value = response
        client = controller.VictoriaMetricsClient("http://vm", transport)
        client.query_cpu("control-1", now)
        queries = [call.args[1]["query"] for call in transport.get.call_args_list]
        self.assertTrue(any("kube_pod_labels" in query and "group_left" in query for query in queries))
        self.assertGreaterEqual(len(queries), 4)

    def test_nvme_uses_raw_timestamp_companion_and_temperature_validation(self):
        transport = Mock()
        evaluation = datetime(2026, 1, 1, tzinfo=UTC)
        metric = {"kubernetes_node": "control-2", "chip": "nvme_nvme0", "sensor": "temp1"}
        def response(_url, params):
            value = str(evaluation.timestamp() - 30) if "timestamp(" in params["query"] else "42"
            return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": metric, "value": [str(evaluation.timestamp()), value]}]}}
        transport.get.side_effect = response
        client = controller.VictoriaMetricsClient("http://vm", transport)
        sample = client.query_nvme("control-2", [("nvme_nvme0", "temp1")], evaluation)[0]
        self.assertEqual(sample.timestamp, datetime.fromtimestamp(evaluation.timestamp() - 30, UTC))
        with self.assertRaises(ValueError):
            client.query_nvme("control-3", [("nvme_nvme0", "temp1")], evaluation)

    def test_nvme_timestamp_query_preserves_each_raw_sensor_timestamp(self):
        transport = Mock()
        evaluation = datetime(2026, 1, 1, tzinfo=UTC)
        sensors = [("nvme_nvme0", "temp1"), ("nvme_nvme0", "temp2")]

        def response(_url, params):
            timestamps = "timestamp(" in params["query"]
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"kubernetes_node": "control-2", "chip": chip, "sensor": sensor},
                            "value": [str(evaluation.timestamp()), str(evaluation.timestamp() - (30 if sensor == "temp1" else 45) if timestamps else 42)],
                        }
                        for chip, sensor in sensors
                    ],
                },
            }

        transport.get.side_effect = response
        client = controller.VictoriaMetricsClient("http://vm", transport)
        samples = client.query_nvme("control-2", sensors, evaluation)
        self.assertEqual([sample.timestamp for sample in samples], [
            datetime.fromtimestamp(evaluation.timestamp() - 30, UTC),
            datetime.fromtimestamp(evaluation.timestamp() - 45, UTC),
        ])
        timestamp_query = transport.get.call_args_list[1].args[1]["query"]
        self.assertEqual(timestamp_query, " or ".join(
            f'timestamp(node_hwmon_temp_celsius{{kubernetes_node="control-2",chip="{chip}",sensor="{sensor}"}})'
            for chip, sensor in sensors
        ))

    def test_cpu_requires_independent_sources_but_allows_zero_xmrig(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "37"]}]}}
        def cpu_response(_url, params):
            query = params["query"]
            if query.startswith("count(kube_pod_labels"):
                return response | {"data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "0"]}]}}
            if "sum by (namespace,pod)" in query:
                return response | {"data": {"resultType": "vector", "result": []}}
            return response
        transport.get.side_effect = cpu_response
        observation = controller.VictoriaMetricsClient("http://vm", transport).query_cpu("control-1", now)
        self.assertIsNone(observation.xmrig)
        self.assertEqual(controller.cpu_value(observation), 37)
        self.assertEqual(len(transport.get.call_args_list), 7)
        queries = [call.args[1]["query"] for call in transport.get.call_args_list]
        self.assertTrue(any("kube_pod_labels" in query and 'node="control-1"' in query for query in queries))

    def test_xmrig_present_on_control1_is_subtracted(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "10"]}]}}
        transport.get.return_value = response
        observation = controller.VictoriaMetricsClient("http://vm", transport).query_cpu("control-1", now)
        self.assertIsNotNone(observation.xmrig)
        self.assertEqual(controller.cpu_value(observation), 0)
        queries = [call.args[1]["query"] for call in transport.get.call_args_list]
        xmrig_query = next(query for query in queries if "sum by (namespace,pod)" in query)
        self.assertLess(xmrig_query.index("sum by (namespace,pod)"), xmrig_query.index("/ count(count"))

    def test_xmrig_present_on_another_node_is_zero_for_control1(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "10"]}]}}
        def another_node(_url, params):
            if params["query"].startswith("count(kube_pod_labels"):
                return response | {"data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "0"]}]}}
            if "sum by (namespace,pod)" in params["query"]:
                return response | {"data": {"resultType": "vector", "result": []}}
            return response
        transport.get.side_effect = another_node
        observation = controller.VictoriaMetricsClient("http://vm", transport).query_cpu("control-1", now)
        self.assertIsNone(observation.xmrig)
        self.assertTrue(any('node="control-1"' in call.args[1]["query"] for call in transport.get.call_args_list))

    def test_existing_xmrig_with_broken_label_join_is_invalid(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "1"]}]}}
        def broken(_url, params):
            if "sum by (namespace,pod)" in params["query"]:
                return response | {"data": {"resultType": "vector", "result": []}}
            if params["query"].startswith("count(kube_pod_labels"):
                return response | {"data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "1"]}]}}
            return response
        transport.get.side_effect = broken
        with self.assertRaises(ValueError):
            controller.VictoriaMetricsClient("http://vm", transport).query_cpu("control-1", now)


class ControllerTests(unittest.TestCase):
    def test_evaluation_failure_is_fail_closed_and_readiness_is_bounded(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("broken")
        guard = controller.GuardController(config(), telemetry, clock=lambda: 100, wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(set(guard.evaluate()), {"control-1", "control-2", "control-3"})
        self.assertTrue(guard.ready)
        self.assertEqual(sum(guard.metrics["safe"].values()), 0)

    def test_observe_never_writes(self):
        guard = controller.GuardController(config(), Mock())
        guard.reconcile("control-2", 50, datetime.now(UTC))

    def test_nodes_fail_independently_and_completed_evaluation_is_ready(self):
        telemetry = Mock()
        source = datetime(2026, 1, 1, tzinfo=UTC)
        def nvme(node, sensors, _evaluation):
            if node == "control-2":
                raise ValueError("control-2")
            return [controller.Source(40, source)] * len(sensors)
        telemetry.query_nvme.side_effect = nvme
        telemetry.query_cpu.return_value = controller.CPUObservation(
            controller.Source(30, datetime(2026, 1, 1, tzinfo=UTC)),
            controller.Source(1, datetime(2026, 1, 1, tzinfo=UTC)),
            controller.Source(1, datetime(2026, 1, 1, tzinfo=UTC)), None)
        guard = controller.GuardController(config(), telemetry, clock=lambda: 100, wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        result = guard.evaluate()
        self.assertTrue(guard.ready)
        self.assertEqual(result["control-2"], 0)
        self.assertIn("control-3", result)
        self.assertEqual(guard.metrics["query_errors"]["control-3"], 0)

    def test_control1_cpu_hysteresis_uses_configured_thresholds_and_dwell(self):
        telemetry = Mock()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        clock = [0]

        def cpu(_node, evaluation, _window):
            source = controller.Source(telemetry.cpu, evaluation)
            return controller.CPUObservation(source, source, source, None, source)

        def nvme(_node, sensors, evaluation):
            return [controller.Source(40, evaluation)] * len(sensors)

        telemetry.query_cpu.side_effect = cpu
        telemetry.query_nvme.side_effect = nvme
        guard = controller.GuardController(config(), telemetry, clock=lambda: clock[0])

        def observe(cpu_percent, seconds):
            telemetry.cpu = cpu_percent
            clock[0] = seconds
            return guard.evaluate(base + timedelta(seconds=seconds))["control-1"]

        self.assertEqual(observe(50, 0), 0)
        self.assertEqual(observe(50, 120), 0)
        self.assertEqual(observe(50, 240), 0)
        self.assertEqual(observe(50, 360), 0)
        self.assertEqual(observe(50, 480), 0)
        self.assertEqual(observe(50, 600), 1)
        self.assertEqual(observe(60, 660), 1)  # middle band preserves state
        self.assertEqual(observe(70, 720), 1)
        self.assertEqual(observe(70, 839), 1)
        self.assertEqual(observe(70, 840), 0)
        self.assertEqual(observe(50, 900), 0)
        self.assertEqual(observe(60, 960), 0)  # middle band interrupts recovery
        self.assertEqual(observe(50, 1020), 0)
        self.assertEqual(observe(50, 1140), 0)
        self.assertEqual(observe(50, 1260), 0)
        self.assertEqual(observe(50, 1380), 0)
        self.assertEqual(observe(50, 1500), 0)
        self.assertEqual(observe(50, 1620), 1)

    def test_health_metrics_are_bounded_and_run_once_evaluates(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("offline")
        telemetry.query_cpu.side_effect = ValueError("offline")
        guard = controller.GuardController(config(), telemetry)
        guard.run_once()
        self.assertEqual(guard.metrics["evaluations"], 1)
        self.assertLessEqual(len(guard.metrics), 12)

    def test_one_source_gap_resets_only_that_node_and_metrics_are_named(self):
        telemetry = Mock()
        first = datetime(2026, 1, 1, tzinfo=UTC)
        second = first + timedelta(seconds=180)
        def nvme(node, sensors, evaluation):
            if evaluation == first:
                return [controller.Source(40, first) for _ in sensors]
            # control-2 has one 180-second source gap, while every source is
            # still fresh at the second evaluation (age is 120 seconds max).
            return [controller.Source(40, first + timedelta(seconds=(150 if node == "control-2" and i == 0 else 60))) for i in range(len(sensors))]
        def cpu(node, evaluation, _window):
            stamp = evaluation - timedelta(seconds=60)
            source = controller.Source(30, stamp)
            return controller.CPUObservation(source, source, source, None)
        telemetry.query_nvme.side_effect = nvme
        telemetry.query_cpu.side_effect = cpu
        guard = controller.GuardController(config(), telemetry, clock=lambda: 1, wall_clock=lambda: first)
        guard.evaluate(first)
        guard.evaluate(second)
        self.assertEqual(guard.metrics["selected_sensor_count"]["control-2"], 0)
        self.assertEqual(guard.metrics["query_errors"]["control-2"], 1)
        self.assertEqual(guard.metrics["selected_sensor_count"]["control-3"], 8)
        self.assertEqual(guard.metrics["query_errors"]["control-3"], 0)
        text = controller.render_metrics(guard)
        self.assertIn("xmrig_guard_nvme_temp_max_celsius", text)
        self.assertIn("xmrig_guard_source_age_seconds", text)
        self.assertIn("xmrig_guard_query_errors_total", text)

    def test_cpu_membership_change_is_fail_closed(self):
        guard = controller.GuardController(config(), Mock())
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        sources = [controller.Source(1, stamp)] * 3
        self.assertTrue(guard._new_source_set("control-1", sources))
        with self.assertRaises(ValueError):
            guard._new_source_set("control-1", sources + [controller.Source(1, stamp)])

    def test_failure_invalidates_values_and_counts_safe_transition(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("offline")
        guard = controller.GuardController(config(), telemetry)
        guard.metrics["safe"]["control-2"] = 1
        guard.evaluate(datetime.now(UTC))
        self.assertEqual(guard.metrics["state_transitions"], 1)
        self.assertTrue(math.isnan(guard.metrics["nvme_temp_max"]["control-2"]))
        self.assertTrue(math.isnan(guard.metrics["source_age_seconds"]["control-2"]))


if __name__ == "__main__":
    unittest.main()
