"""Small, dependency-free XMRig safety signal controller.

The controller deliberately treats telemetry as untrusted input.  A complete
set of fresh samples is required before a node can become safe.  Policy is
code: changing thresholds, sensors, or timing requires a reviewed diff here.
"""
import json
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENSORS = {
    "control-1": (),
    "control-2": (("nvme_nvme0", "temp1"), ("nvme_nvme0", "temp2"), ("nvme_nvme0", "temp3"), ("nvme_nvme1", "temp1"), ("nvme_nvme1", "temp2"), ("nvme_nvme1", "temp3"), ("nvme_nvme1", "temp4")),
    "control-3": (("nvme_nvme0", "temp1"), ("nvme_nvme0", "temp2"), ("nvme_nvme0", "temp3"), ("nvme_nvme0", "temp4"), ("nvme_nvme1", "temp1"), ("nvme_nvme1", "temp2"), ("nvme_nvme1", "temp3"), ("nvme_nvme1", "temp4")),
}
ENDPOINT = "http://vmauth-victoria-metrics.observability.svc.cluster.local:8427"
EVALUATION_INTERVAL_SECONDS = 60
SOURCE_SAMPLE_MAX_AGE_SECONDS = 120
MAX_SOURCE_GAP_SECONDS = 120
HTTP_TIMEOUT_SECONDS = 10


def _dt(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite timestamp")
    return datetime.fromtimestamp(value, timezone.utc)


def _fresh(timestamp, evaluation, max_age):
    """Return whether a source timestamp is not future-dated or too old."""
    age = (evaluation - timestamp).total_seconds()
    return 0 <= age <= max_age


class DwellPolicy:
    def __init__(self, recovery_limit, trip_limit, recovery_dwell, trip_dwell, max_gap_seconds=120):
        self.recovery_limit, self.trip_limit = recovery_limit, trip_limit
        self.recovery_dwell, self.trip_dwell = recovery_dwell, trip_dwell
        self.max_gap = float(max_gap_seconds)
        self.safe = False
        self._last_source = None
        self._pending = None
        self._since = None

    def invalidate(self):
        self.safe = False
        self._last_source = self._pending = self._since = None

    def observe(self, value, source_time, monotonic_now):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not isinstance(source_time, datetime):
            self.invalidate()
            return False
        source_seconds = source_time.timestamp()
        if self._last_source is not None:
            gap = source_seconds - self._last_source
            if gap <= 0:
                return self.safe  # duplicate/out-of-order samples cannot advance dwell
            if gap > self.max_gap:
                self._pending = self._since = None
        self._last_source = source_seconds
        kind = "recover" if value <= self.recovery_limit else "trip" if value >= self.trip_limit else None
        if kind is None:
            self._pending = self._since = None
            return self.safe
        if (kind == "recover") == self.safe:
            self._pending = self._since = None
            return self.safe
        if kind != self._pending:
            self._pending, self._since = kind, monotonic_now
        else:
            dwell = self.recovery_dwell if kind == "recover" else self.trip_dwell
            if monotonic_now - self._since >= dwell:
                self.safe = kind == "recover"
                self._pending = self._since = None
        return self.safe


@dataclass(frozen=True)
class Source:
    value: float
    timestamp: datetime


@dataclass(frozen=True)
class CPUObservation:
    host: Source
    xmrig: Source | None
    presence: Source


def cpu_value(observation):
    if not isinstance(observation.host, Source):
        raise ValueError("host source is required")
    xmrig = observation.xmrig.value if observation.xmrig else 0.0
    return max(0.0, min(100.0, observation.host.value - xmrig))


class VictoriaMetricsClient:
    def __init__(self, endpoint, transport=None, timeout=10, step_seconds=120):
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport or _HTTPTransport(timeout)
        self.step = f"{int(step_seconds)}s"

    def _query(self, expression, evaluation):
        # explicit step: default 5m step makes timestamp(a or b) snap to 5-min boundaries,
        # which made 60% of freshness checks fail; step=max_age also bounds VM lookbehind
        params = {"query": expression, "time": evaluation.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "step": self.step}
        try:
            payload = self.transport.get(self.endpoint + "/api/v1/query", params)
        except urllib.error.HTTPError as exc:
            # a rejected query is undiagnosable without its text: 40h of bare 422s went unseen
            raise ValueError(f"VictoriaMetrics rejected ({exc.code}): {expression}") from exc
        if payload.get("status") != "success" or payload.get("data", {}).get("resultType") != "vector" or not isinstance(payload["data"].get("result"), list):
            raise ValueError(f"invalid VictoriaMetrics response for: {expression}")
        return payload["data"]["result"]

    @staticmethod
    def _sources(rows, identity=None, raw_timestamp=False):
        out = {}
        for row in rows:
            metric = row.get("metric", {})
            key = identity(metric) if identity else tuple(sorted(metric.items()))
            if key in out or not isinstance(row.get("value"), list) or len(row["value"]) != 2:
                raise ValueError("malformed or duplicate telemetry")
            try:
                value = float(row["value"][1])
                timestamp = _dt(row["value"][1] if raw_timestamp else row["value"][0])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("malformed telemetry value") from exc
            if not math.isfinite(value):
                raise ValueError("non-finite telemetry value")
            out[key] = Source(value, timestamp)
        return out

    def query_nvme(self, node, sensors, evaluation):
        parts = [f'node_hwmon_temp_celsius{{kubernetes_node="{node}",chip="{chip}",sensor="{sensor}"}}' for chip, sensor in sensors]
        expression = " or ".join(parts)
        rows = self._query(expression, evaluation)
        # timestamp() over an or-expression becomes a step-aligned subquery in VictoriaMetrics
        # (fake boundary stamps broke 60% of freshness checks); single selectors return raw stamps
        timestamps = [row for part in parts for row in self._query("timestamp(" + part + ")", evaluation)]
        key = lambda m: (m.get("chip"), m.get("sensor"))
        if any(row.get("metric", {}).get("kubernetes_node") != node for row in rows + timestamps):
            raise ValueError("NVMe node identity changed")
        found = self._sources(rows, key)
        stamped = self._sources(timestamps, key, raw_timestamp=True)
        if set(found) != set(sensors):
            raise ValueError("incomplete or changed NVMe identity set")
        if set(stamped) != set(sensors):
            raise ValueError("incomplete or changed NVMe timestamp identity set")
        result = []
        for item in sensors:
            if not -40 <= found[item].value <= 150:
                raise ValueError("implausible NVMe temperature")
            result.append(Source(found[item].value, stamped[item].timestamp))
        return result

    def query_cpu(self, node, evaluation, window="5m"):
        host_raw = f'node_cpu_seconds_total{{kubernetes_node="{node}",mode!="idle"}}'
        idle_raw = f'node_cpu_seconds_total{{kubernetes_node="{node}",mode="idle"}}'
        cadvisor_raw = f'container_cpu_usage_seconds_total{{node="{node}",namespace="web3",container!="",container!="POD"}}'
        host_query = f'sum(rate({host_raw}[{window}])) / count(count({idle_raw}) by (cpu)) * 100'
        ksm_raw = 'kube_pod_info{namespace="web3"}'
        label_selector = 'namespace="web3",label_app_kubernetes_io_component="thermal-guarded"'
        node_pods_raw = f'kube_pod_info{{namespace="web3",node="{node}"}}'
        xmrig_presence_raw = f'kube_pod_labels{{{label_selector}}} * on(namespace,pod) group_left(node) {node_pods_raw}'
        xmrig_presence_query = f'count({xmrig_presence_raw}) or vector(0)'
        xmrig_query = f'100 * (sum((sum by (namespace,pod) (rate({cadvisor_raw}[{window}])) * on(namespace,pod) group_left(node) ({xmrig_presence_raw})) or vector(0)) / count(count({idle_raw}) by (cpu)))'
        def one(query, optional=False, raw_timestamp=False):
            rows = self._query(query, evaluation)
            if not rows and optional:
                return None
            values = self._sources(rows, raw_timestamp=raw_timestamp)
            if len(values) != 1:
                raise ValueError("CPU source must be one scalar")
            return next(iter(values.values()))
        def source(query, raw_selector, optional=False):
            value = one(query, optional)
            if value is None:
                if optional:
                    return None
                raise ValueError("incomplete CPU source")
            selectors = (raw_selector,) if isinstance(raw_selector, str) else raw_selector
            stamps = []
            for selector in selectors:
                rows = self._query("timestamp(" + selector + ")", evaluation)
                if not rows:
                    raise ValueError("missing raw CPU timestamps")
                stamps.extend(self._sources(rows, raw_timestamp=True).values())
            return Source(value.value, min(item.timestamp for item in stamps))
        presence_rows = self._query(xmrig_presence_query, evaluation)
        presence_values = self._sources(presence_rows)
        if len(presence_values) != 1 or next(iter(presence_values.values())).value < 0:
            raise ValueError("invalid labelled XMRig presence source")
        presence = next(iter(presence_values.values()))
        if presence.value > 0:
            presence_timestamps = []
            for raw_selector in (f'kube_pod_labels{{{label_selector}}}', node_pods_raw):
                rows = self._query("timestamp(" + raw_selector + ")", evaluation)
                if not rows:
                    raise ValueError("missing raw XMRig membership timestamps")
                presence_timestamps.extend(self._sources(rows, raw_timestamp=True).values())
            presence = Source(presence.value, min(item.timestamp for item in presence_timestamps))
            # xmrig's raw selectors include cadvisor_raw, so cadvisor freshness is
            # verified exactly when its data enters the subtraction
            xmrig = source(
                xmrig_query,
                (cadvisor_raw, f'kube_pod_labels{{{label_selector}}}', node_pods_raw),
            )
        else:
            # no labelled miner on this node: anchor presence freshness to pod-info stamps
            rows = self._query("timestamp(" + ksm_raw + ")", evaluation)
            if not rows:
                raise ValueError("missing raw pod-info timestamps")
            presence = Source(0, min(item.timestamp for item in self._sources(rows, raw_timestamp=True).values()))
            xmrig = None
        return CPUObservation(source(host_query, host_raw), xmrig, presence)


class _HTTPTransport:
    def __init__(self, timeout=10, headers=None, context=None):
        self.timeout, self.headers, self.context = timeout, headers or {}, context

    def get(self, url, params):
        request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=self.headers)
        with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
            return json.load(response)


class GuardController:
    def __init__(self, telemetry, clock=time.monotonic, wall_clock=lambda: datetime.now(timezone.utc)):
        self.telemetry = telemetry
        self.clock, self.wall_clock = clock, wall_clock
        self.policies = {"control-2": DwellPolicy(60, 70, 600, 120, MAX_SOURCE_GAP_SECONDS), "control-3": DwellPolicy(60, 70, 600, 120, MAX_SOURCE_GAP_SECONDS)}
        self.cpu_policy = DwellPolicy(50, 70, 600, 120, MAX_SOURCE_GAP_SECONDS)
        self.ready = False
        self.metrics = {
            "evaluations": 0, "query_errors": {node: 0 for node in SENSORS},
            "safe": {node: 0 for node in SENSORS},
            "nvme_temp_max": {node: 0.0 for node in SENSORS if node != "control-1"},
            "source_age_seconds": {node: 0.0 for node in SENSORS},
            "cpu_non_xmrig": 0.0,
        }
        self._last_source_stamps = {node: () for node in SENSORS}

    def _new_source_set(self, node, sources):
        stamps = tuple(source.timestamp.timestamp() for source in sources)
        previous = self._last_source_stamps[node]
        if previous and len(previous) != len(stamps):
            raise ValueError("CPU source membership changed")
        if previous and len(previous) == len(stamps) and any(current - old > MAX_SOURCE_GAP_SECONDS for current, old in zip(stamps, previous)):
            raise ValueError("source gap exceeded maximum")
        if previous and len(previous) == len(stamps) and any(current <= old for current, old in zip(stamps, previous)):
            return False
        self._last_source_stamps[node] = stamps
        return True

    def evaluate(self, evaluation=None):
        evaluation = evaluation or self.wall_clock()
        now = self.clock()
        for node, sensors in SENSORS.items():
            try:
                if node == "control-1":
                    obs = self.telemetry.query_cpu(node, evaluation)
                    sources = (obs.host, obs.presence) + ((obs.xmrig,) if obs.xmrig else ())
                    if not all(_fresh(source.timestamp, evaluation, SOURCE_SAMPLE_MAX_AGE_SECONDS) for source in sources):
                        raise ValueError("stale or future CPU source")
                    if self._new_source_set(node, sources):
                        safe = self.cpu_policy.observe(cpu_value(obs), min(source.timestamp for source in sources), now)
                    else:
                        safe = self.cpu_policy.safe
                    self.metrics["cpu_non_xmrig"] = cpu_value(obs)
                    self.metrics["source_age_seconds"][node] = max(0.0, evaluation.timestamp() - min(source.timestamp for source in sources).timestamp())
                else:
                    samples = self.telemetry.query_nvme(node, sensors, evaluation)
                    if not samples or not all(_fresh(sample.timestamp, evaluation, SOURCE_SAMPLE_MAX_AGE_SECONDS) for sample in samples):
                        raise ValueError("stale or future NVMe source")
                    if self._new_source_set(node, samples):
                        safe = self.policies[node].observe(max(sample.value for sample in samples), max(sample.timestamp for sample in samples), now)
                    else:
                        safe = self.policies[node].safe
                    self.metrics["nvme_temp_max"][node] = max(sample.value for sample in samples)
                    self.metrics["source_age_seconds"][node] = max(0.0, evaluation.timestamp() - min(sample.timestamp for sample in samples).timestamp())
                self.metrics["safe"][node] = int(safe)
            except Exception as exc:
                # one line per failure, no traceback: the query text travels in the exception
                logging.error(f"evaluation failed for {node}: {exc!r}")
                self.metrics["query_errors"][node] += 1
                policy = self.cpu_policy if node == "control-1" else self.policies[node]
                policy.invalidate()
                self._last_source_stamps[node] = ()
                self.metrics["safe"][node] = 0
                self.metrics["source_age_seconds"][node] = float("nan")
                if node == "control-1":
                    self.metrics["cpu_non_xmrig"] = float("nan")
                else:
                    self.metrics["nvme_temp_max"][node] = float("nan")
        self.metrics["evaluations"] += 1
        self.ready = True
        return dict(self.metrics["safe"])


def render_metrics(controller):
    m = controller.metrics
    lines = [
        f'xmrig_guard_evaluations_total {m["evaluations"]}',
        f'xmrig_guard_cpu_non_xmrig_percent {m["cpu_non_xmrig"]}',
    ]
    for metric, values in (
        ("safe", m["safe"]),
        ("query_errors_total", m["query_errors"]),
        ("source_age_seconds", m["source_age_seconds"]),
        ("nvme_temp_max_celsius", m["nvme_temp_max"]),
    ):
        metric_name = "xmrig_guard_" + metric
        lines.extend(f'{metric_name}{{node="{node}"}} {value}' for node, value in values.items())
    return "\n".join(lines) + "\n"


class _StatusHandler(BaseHTTPRequestHandler):
    controller = None  # assigned before the server starts
    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, "ok\n", "text/plain")
        elif self.path == "/readyz":
            self._send(200 if self.controller.ready else 503, "ready\n" if self.controller.ready else "not ready\n", "text/plain")
        elif self.path == "/metrics":
            body = render_metrics(self.controller)
            self._send(200, body, "text/plain; version=0.0.4")
        else:
            self._send(404, "not found\n", "text/plain")
    def _send(self, status, body, content_type):
        data = body.encode()
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, format, *args):
        return


def main():
    controller = GuardController(VictoriaMetricsClient(ENDPOINT, timeout=HTTP_TIMEOUT_SECONDS, step_seconds=SOURCE_SAMPLE_MAX_AGE_SECONDS))
    _StatusHandler.controller = controller
    server = ThreadingHTTPServer(("0.0.0.0", 8080), _StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        controller.evaluate()
        time.sleep(EVALUATION_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
