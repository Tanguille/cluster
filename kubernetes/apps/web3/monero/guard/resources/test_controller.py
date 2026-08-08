import math
import os
import re
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(__file__))
import controller

UTC = timezone.utc
# The other two legs of the trip-to-drain budget live in the miner's manifest, so they are read
# from it rather than copied: a manifest-only change must fail the budget test, not pass it.
RESOURCESET = os.path.join(os.path.dirname(__file__), "..", "..", "xmrig", "resourceset.yaml")


def manifest_seconds(field):
    with open(RESOURCESET) as handle:
        found = re.findall(rf"^\s*{field}:\s*(\d+)\s*(?:#.*)?$", handle.read(), re.MULTILINE)
    if len(found) != 1:
        raise AssertionError(f"expected exactly one {field} in resourceset.yaml, found {len(found)}")
    return int(found[0])


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
        self.assertEqual(transport.get.call_args.args[1]["time"], "2026-01-01T00:00:00Z")
        self.assertEqual(transport.get.call_args.args[1]["step"], "120s")
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

    def test_cpu_zero_xmrig_uses_pod_info_anchor_and_four_round_trips(self):
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
        self.assertEqual(len(transport.get.call_args_list), 4)
        queries = [call.args[1]["query"] for call in transport.get.call_args_list]
        self.assertTrue(any("kube_pod_labels" in query and 'node="control-1"' in query for query in queries))
        self.assertTrue(any(query.startswith('timestamp(kube_pod_info{namespace="web3"}') for query in queries))

    def test_xmrig_present_on_control1_is_subtracted(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "10"]}]}}
        transport.get.return_value = response
        observation = controller.VictoriaMetricsClient("http://vm", transport).query_cpu("control-1", now)
        self.assertIsNotNone(observation.xmrig)
        self.assertEqual(controller.cpu_value(observation), 0)
        queries = [call.args[1]["query"] for call in transport.get.call_args_list]
        # membership stamps date both the presence count and the subtraction; fetching them
        # twice cost 9 round trips, so this pins the reuse without pinning a query count
        self.assertEqual(len(queries), len(set(queries)))
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

    def test_http_rejection_carries_the_query_text(self):
        transport = Mock()
        transport.get.side_effect = urllib.error.HTTPError("http://vm", 422, "Unprocessable Entity", None, None)
        client = controller.VictoriaMetricsClient("http://vm", transport)
        with self.assertRaisesRegex(ValueError, "node_hwmon_temp_celsius"):
            client.query_nvme("control-2", [("nvme_nvme0", "temp1")], datetime(2026, 1, 1, tzinfo=UTC))

    def test_all_generated_queries_have_balanced_parentheses(self):
        transport = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        response = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [str(now.timestamp()), "10"]}]}}
        transport.get.return_value = response
        client = controller.VictoriaMetricsClient("http://vm", transport)
        client.query_cpu("control-1", now)  # presence > 0 exercises the xmrig branch
        with self.assertRaises(ValueError):  # identity check fails on the generic mock after issuing both queries
            client.query_nvme("control-2", [("nvme_nvme0", "temp1")], now)
        self.assertGreaterEqual(len(transport.get.call_args_list), 8)
        for call in transport.get.call_args_list:
            query = call.args[1]["query"]
            depth = 0
            for char in query:
                depth += char == "("
                depth -= char == ")"
                self.assertGreaterEqual(depth, 0, query)
            self.assertEqual(depth, 0, query)

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
    def test_audited_sensor_sets(self):
        # Composite (temp1) per drive, two drives per node; die sensors are excluded
        self.assertEqual(len(controller.SENSORS["control-2"]), 2)
        self.assertEqual(len(controller.SENSORS["control-3"]), 2)
        self.assertTrue(all(sensor == "temp1" for node in ("control-2", "control-3") for _, sensor in controller.SENSORS[node]))

    def test_evaluation_failure_is_fail_closed_and_readiness_is_bounded(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("broken")
        guard = controller.GuardController(telemetry, clock=lambda: 100, wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(set(guard.evaluate()), {"control-1", "control-2", "control-3"})
        self.assertTrue(guard.ready)
        self.assertEqual(sum(guard.metrics["safe"].values()), 0)

    def test_nodes_fail_independently_and_completed_evaluation_is_ready(self):
        telemetry = Mock()
        source = datetime(2026, 1, 1, tzinfo=UTC)
        def nvme(node, sensors, _evaluation):
            if node == "control-2":
                raise ValueError("control-2")
            return [controller.Source(40, source)] * len(sensors)
        telemetry.query_nvme.side_effect = nvme
        telemetry.query_cpu.return_value = controller.CPUObservation(
            controller.Source(30, source), None, controller.Source(0, source))
        guard = controller.GuardController(telemetry, clock=lambda: 100, wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        result = guard.evaluate()
        self.assertTrue(guard.ready)
        self.assertEqual(result["control-2"], 0)
        self.assertIn("control-3", result)
        self.assertEqual(guard.metrics["query_errors"]["control-3"], 0)

    def test_control1_cpu_hysteresis_uses_configured_thresholds_and_dwell(self):
        telemetry = Mock()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        clock = [0]

        def cpu(_node, evaluation):
            source = controller.Source(telemetry.cpu, evaluation)
            return controller.CPUObservation(source, None, source)

        def nvme(_node, sensors, evaluation):
            return [controller.Source(40, evaluation)] * len(sensors)

        telemetry.query_cpu.side_effect = cpu
        telemetry.query_nvme.side_effect = nvme
        guard = controller.GuardController(telemetry, clock=lambda: clock[0])

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

    def test_health_metrics_are_bounded(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("offline")
        telemetry.query_cpu.side_effect = ValueError("offline")
        guard = controller.GuardController(telemetry)
        guard.evaluate()
        self.assertEqual(guard.metrics["evaluations"], 1)

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
        def cpu(node, evaluation):
            stamp = evaluation - timedelta(seconds=60)
            source = controller.Source(30, stamp)
            return controller.CPUObservation(source, None, source)
        telemetry.query_nvme.side_effect = nvme
        telemetry.query_cpu.side_effect = cpu
        guard = controller.GuardController(telemetry, clock=lambda: 1, wall_clock=lambda: first)
        guard.evaluate(first)
        guard.evaluate(second)
        self.assertEqual(guard.metrics["query_errors"]["control-2"], 1)
        self.assertEqual(guard.metrics["query_errors"]["control-3"], 0)
        text = controller.render_metrics(guard)
        self.assertIn("xmrig_guard_nvme_temp_max_celsius", text)
        self.assertIn("xmrig_guard_source_age_seconds", text)
        self.assertIn("xmrig_guard_query_errors_total", text)

    def test_miner_arriving_exempts_only_the_new_source(self):
        # a miner starting adds the xmrig source, which must not fail the node closed
        guard = controller.GuardController(Mock())
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        base = {"host": controller.Source(1, stamp), "presence": controller.Source(1, stamp)}
        self.assertTrue(guard._new_source_set("control-1", base))
        later = {key: controller.Source(1, stamp + timedelta(seconds=30)) for key in base}
        self.assertTrue(guard._new_source_set("control-1", later | {"xmrig": controller.Source(1, stamp)}))
        # the shared keys are still checked: unchanged stamps mean no advancement
        self.assertFalse(guard._new_source_set("control-1", later | {"xmrig": controller.Source(1, stamp)}))

    def test_miner_starting_and_stopping_never_faults_control1(self):
        # the regression this replaces: the xmrig source appearing and disappearing changed the
        # sample count, read as tampering, and drained the miner it was caused by
        telemetry = Mock()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        mining = [False]

        def cpu(_node, evaluation):
            source = controller.Source(30, evaluation)
            return controller.CPUObservation(source, controller.Source(5, evaluation) if mining[0] else None, source)

        telemetry.query_cpu.side_effect = cpu
        telemetry.query_nvme.side_effect = lambda _n, sensors, evaluation: [controller.Source(40, evaluation)] * len(sensors)
        guard = controller.GuardController(telemetry, clock=lambda: 0)
        for step, running in enumerate([False, True, True, False, True]):
            mining[0] = running
            guard.evaluate(base + timedelta(seconds=30 * step))
        self.assertEqual(guard.metrics["query_errors"]["control-1"], 0)

    def test_nvme_source_keys_survive_reordering(self):
        # keys are the sensor identities themselves, so iteration order must not matter
        guard = controller.GuardController(Mock())
        first, second = controller.SENSORS["control-2"]
        base = datetime(2026, 1, 1, tzinfo=UTC)
        previous = {
            first: controller.Source(40, base),
            second: controller.Source(41, base + timedelta(seconds=30)),
        }
        self.assertTrue(guard._new_source_set("control-2", previous))
        # advance each sensor's own timestamp, then reverse the iteration order
        reordered = {
            second: controller.Source(41, base + timedelta(seconds=60)),
            first: controller.Source(40, base + timedelta(seconds=30)),
        }
        self.assertTrue(guard._new_source_set("control-2", reordered))
        # and the anti-replay check still binds across the reorder
        self.assertFalse(guard._new_source_set("control-2", reordered))

    def test_shared_key_gap_fails_closed_even_when_a_source_is_added(self):
        guard = controller.GuardController(Mock())
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        base = {"host": controller.Source(1, stamp), "presence": controller.Source(1, stamp)}
        self.assertTrue(guard._new_source_set("control-1", base))
        far = stamp + timedelta(seconds=guard.policies["control-1"].max_gap + 1)
        stale = {key: controller.Source(1, far) for key in base}
        with self.assertRaises(ValueError):
            guard._new_source_set("control-1", stale | {"xmrig": controller.Source(1, far)})

    def test_panic_limit_trips_without_dwell_and_needs_full_recovery(self):
        p = controller.DwellPolicy(62, 65, 300, 60, 120, panic_limit=68)
        source = datetime(2026, 1, 1, tzinfo=UTC)
        p.safe = True
        self.assertFalse(p.observe(68, source, 0.0))
        self.assertFalse(p.safe)
        # recovery is not instant afterwards: the full 300s dwell must elapse below 62C
        self.assertFalse(p.observe(60, source + timedelta(seconds=1), 1.0))
        self.assertFalse(p.observe(60, source + timedelta(seconds=2), 299.0))
        self.assertTrue(p.observe(60, source + timedelta(seconds=3), 302.0))

    def test_trip_to_drain_budget_fits_the_thermal_margin(self):
        # The 65C trip is sized on a 135s chain against a 70C rating. Two legs are policy here,
        # two are read from the miner's manifest, so either side drifting fails this. The rate is
        # the p99 of 1151 measured miner starts, not the p90 1.1C/min the comments used to quote.
        keda_poll = manifest_seconds("pollingInterval")
        drain = manifest_seconds("terminationGracePeriodSeconds")
        rise_c_per_min, rating = 1.35, 70
        policies = controller.GuardController(Mock()).policies
        for node in ("control-2", "control-3"):
            policy = policies[node]
            budget = controller.EVALUATION_INTERVAL_SECONDS + policy.trip_dwell + keda_poll + drain
            peak = policy.trip_limit + rise_c_per_min * budget / 60
            self.assertLessEqual(budget, 135, node)
            self.assertLess(peak, rating, f"{node} peaks at {peak}C against a {rating}C rating")
            # At the p99 rate the trip path models 68.0C, above the panic limit, so panic is what
            # actually sheds the miner on a fast ramp. Its budget is the same chain without the dwell.
            panic_budget = controller.EVALUATION_INTERVAL_SECONDS + keda_poll + drain
            panic_peak = policy.panic_limit + rise_c_per_min * panic_budget / 60
            self.assertLess(policy.panic_limit, peak, f"{node} panic is not the governing path")
            self.assertLess(panic_peak, rating, f"{node} panics to {panic_peak}C against {rating}C")

    def test_nvme_policies_carry_the_panic_limit_and_control1_does_not(self):
        guard = controller.GuardController(Mock())
        self.assertEqual(guard.policies["control-2"].panic_limit, 67)
        self.assertEqual(guard.policies["control-3"].panic_limit, 67)
        self.assertIsNone(guard.policies["control-1"].panic_limit)

    def test_cpu_gated_node_tolerates_the_cadvisor_scrape_lag(self):
        # A 150s-old CPU sample is within cadvisor's 60s scrape reality but was over the 120s
        # NVMe budget, which invalidated control-1 465 times in 10d and drained its miner.
        # Two evaluations 150s apart, so the source-gap branch runs too: freshness, the gap check
        # and the dwell reset all read one per-node budget and a 150s gap must clear none of them.
        telemetry = Mock()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        def cpu(_node, evaluation):
            source = controller.Source(30, evaluation - timedelta(seconds=150))
            return controller.CPUObservation(source, None, source)
        telemetry.query_cpu.side_effect = cpu
        telemetry.query_nvme.side_effect = ValueError("not exercised")
        guard = controller.GuardController(telemetry, clock=lambda: 0, wall_clock=lambda: base)
        guard.evaluate(base)
        guard.evaluate(base + timedelta(seconds=150))
        self.assertEqual(guard.metrics["query_errors"]["control-1"], 0)
        # the NVMe nodes keep the tighter budget: their sources all scrape at 20s
        self.assertEqual(guard.policies["control-1"].max_gap, controller.CPU_SAMPLE_MAX_AGE_SECONDS)
        self.assertEqual(guard.policies["control-2"].max_gap, controller.SOURCE_SAMPLE_MAX_AGE_SECONDS)

    def test_failure_invalidates_values(self):
        telemetry = Mock()
        telemetry.query_nvme.side_effect = ValueError("offline")
        guard = controller.GuardController(telemetry)
        guard.metrics["safe"]["control-2"] = 1
        guard.evaluate(datetime.now(UTC))
        self.assertEqual(guard.metrics["safe"]["control-2"], 0)
        self.assertTrue(math.isnan(guard.metrics["nvme_temp_max"]["control-2"]))
        self.assertTrue(math.isnan(guard.metrics["source_age_seconds"]["control-2"]))


if __name__ == "__main__":
    unittest.main()
