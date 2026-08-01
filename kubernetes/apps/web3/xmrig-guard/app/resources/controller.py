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

# temp1 is the Composite sensor, which is what the drives' 70C rating specifies and
# what smartctl reports. temp2-temp4 are internal die sensors that run ~9C hotter and
# carry no comparable rating, so including them gated a Composite threshold against
# the wrong reading. Fewer series also means fewer chances for a single missing sample
# to fail the identity check and latch a node closed.
SENSORS = {
    # empty tuple = no NVMe visible to this node (control-1 is a VM); it is gated on CPU
    # headroom instead, which is what every `if sensors` branch below keys off.
    "control-1": (),
    "control-2": (("nvme_nvme0", "temp1"), ("nvme_nvme1", "temp1")),
    "control-3": (("nvme_nvme0", "temp1"), ("nvme_nvme1", "temp1")),
}
# Miner slot priority, best first. control-1 has no NVMe to cook and was safe 96% of the
# last 7d against 60%/30% for the bare-metal nodes. Each ScaledObject counts how many safe
# nodes outrank it and subtracts one miner's draw per rank, so this order decides who gets
# scarce watts. It lives here rather than in the manifests because it is a property of the
# node telemetry, and a future headroom-derived ranking replaces this tuple alone.
PRIORITY = ("control-1", "control-2", "control-3")
ENDPOINT = "http://vmauth-victoria-metrics.observability.svc.cluster.local:8427"
EVALUATION_INTERVAL_SECONDS = 30
SOURCE_SAMPLE_MAX_AGE_SECONDS = 120
MAX_SOURCE_GAP_SECONDS = 120
# 7 serial queries per CPU evaluation: at 10s a hung VictoriaMetrics blocked 70s against a 30s interval
HTTP_TIMEOUT_SECONDS = 5


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
    def __init__(self, recovery_limit, trip_limit, recovery_dwell, trip_dwell, max_gap_seconds=120, panic_limit=None):
        self.recovery_limit, self.trip_limit = recovery_limit, trip_limit
        self.recovery_dwell, self.trip_dwell = recovery_dwell, trip_dwell
        self.panic_limit = panic_limit
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
        # third, dwell-free state: 2C from the rating, confirming the reading over a 60s dwell
        # would spend most of the margin. Clearing _pending forces a full recovery dwell after.
        if self.panic_limit is not None and value >= self.panic_limit:
            self.safe = False
            self._pending = self._since = None
            return False
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
    def __init__(self, endpoint, transport=None, timeout=HTTP_TIMEOUT_SECONDS, step_seconds=120):
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
        labels_raw = f'kube_pod_labels{{{label_selector}}}'
        def one(query):
            values = self._sources(self._query(query, evaluation))
            if len(values) != 1:
                raise ValueError(f"CPU source must be one scalar: {query}")
            return next(iter(values.values()))
        def oldest(*selectors):
            out = []
            for selector in selectors:
                rows = self._query("timestamp(" + selector + ")", evaluation)
                if not rows:
                    raise ValueError(f"missing raw timestamps: {selector}")
                out.extend(self._sources(rows, raw_timestamp=True).values())
            return min(item.timestamp for item in out)
        presence = one(xmrig_presence_query)
        if presence.value < 0:
            raise ValueError("invalid labelled XMRig presence source")
        if presence.value > 0:
            # the membership stamps date both the presence count and the subtraction, so
            # they are fetched once and reused rather than queried twice per evaluation
            membership = oldest(labels_raw, node_pods_raw)
            presence = Source(presence.value, membership)
            # cadvisor joins the same membership selectors, so cadvisor freshness is
            # verified exactly when its data enters the subtraction
            xmrig = Source(one(xmrig_query).value, min(membership, oldest(cadvisor_raw)))
        else:
            # no labelled miner on this node: anchor presence freshness to pod-info stamps
            presence = Source(0, oldest(ksm_raw))
            xmrig = None
        host = Source(one(host_query).value, oldest(host_raw))
        return CPUObservation(host, xmrig, presence)


class _HTTPTransport:
    def __init__(self, timeout=HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout

    def get(self, url, params):
        request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)


class GuardController:
    def __init__(self, telemetry, clock=time.monotonic, wall_clock=lambda: datetime.now(timezone.utc)):
        self.telemetry = telemetry
        self.clock, self.wall_clock = clock, wall_clock
        # Trip 65C / recover 62C on Composite, against a 70C drive rating. Mining raises
        # Composite at up to 1.1C/min. Trip-to-drain is 135s (30s evaluation + 60s dwell + 30s
        # KEDA poll + 15s drain), so 2.5C of rise to a ~67.5C peak; test_trip_to_drain_budget
        # pins it. The 68C panic trip is dwell-free and peaks near 69.4C.
        # The drives idle in the 60-64C band on warm afternoons, so recovery sits at 62C:
        # replaying 7d puts control-2/3 at 55.0%/38.8% safe against 44.7%/31.5% at 60C/600s.
        # control-1 is keyed the same way on CPU headroom rather than temperature; SENSORS
        # decides which source feeds which node, so the policy dict needs no special case. No
        # panic limit there: a busy CPU has no equivalent of a drive's absolute rating.
        self.policies = {
            "control-1": DwellPolicy(50, 70, 600, 120, MAX_SOURCE_GAP_SECONDS),
            "control-2": DwellPolicy(62, 65, 300, 60, MAX_SOURCE_GAP_SECONDS, panic_limit=68),
            "control-3": DwellPolicy(62, 65, 300, 60, MAX_SOURCE_GAP_SECONDS, panic_limit=68),
        }
        self.ready = False
        self.metrics = {
            "evaluations": 0, "query_errors": {node: 0 for node in SENSORS},
            "safe": {node: 0 for node in SENSORS},
            "nvme_temp_max": {node: 0.0 for node in SENSORS if SENSORS[node]},
            "source_age_seconds": {node: 0.0 for node in SENSORS},
            "cpu_non_xmrig": {node: 0.0 for node in SENSORS if not SENSORS[node]},
            "rank": {node: PRIORITY.index(node) for node in SENSORS},
        }
        self._last_source_stamps = {node: {} for node in SENSORS}

    def _new_source_set(self, node, sources):
        # Keyed by source identity, not position: a miner starting or stopping adds or removes
        # the xmrig source, and comparing by position made that look like tampering. It failed
        # control-1 closed 19 times in 12h, draining the miner, which changed the set back.
        # Only keys in both sets are comparable; a genuinely new key is exempt for one cycle
        # rather than the whole evaluation going unchecked.
        stamps = {key: source.timestamp.timestamp() for key, source in sources.items()}
        previous = self._last_source_stamps[node]
        shared = stamps.keys() & previous.keys()
        if any(stamps[key] - previous[key] > MAX_SOURCE_GAP_SECONDS for key in shared):
            raise ValueError("source gap exceeded maximum")
        if shared and any(stamps[key] <= previous[key] for key in shared):
            return False
        self._last_source_stamps[node] = stamps
        return True

    def evaluate(self, evaluation=None):
        evaluation = evaluation or self.wall_clock()
        now = self.clock()
        for node, sensors in SENSORS.items():
            try:
                # no sensors means a node with no visible NVMe (control-1, a VM): it is gated
                # on CPU headroom instead. The dwell policy and metrics are keyed identically.
                if sensors:
                    samples = dict(zip(sensors, self.telemetry.query_nvme(node, sensors, evaluation)))
                    # trip on the hottest drive, date it by the newest sample it was read from
                    value = max(item.value for item in samples.values())
                    stamp = max(item.timestamp for item in samples.values())
                    self.metrics["nvme_temp_max"][node] = value
                else:
                    obs = self.telemetry.query_cpu(node, evaluation)
                    samples = {"host": obs.host, "presence": obs.presence}
                    if obs.xmrig:
                        samples["xmrig"] = obs.xmrig
                    value = cpu_value(obs)
                    stamp = min(item.timestamp for item in samples.values())
                    self.metrics["cpu_non_xmrig"][node] = value
                if not samples or not all(_fresh(item.timestamp, evaluation, SOURCE_SAMPLE_MAX_AGE_SECONDS) for item in samples.values()):
                    raise ValueError("stale or future source")
                policy = self.policies[node]
                safe = policy.observe(value, stamp, now) if self._new_source_set(node, samples) else policy.safe
                self.metrics["source_age_seconds"][node] = max(0.0, evaluation.timestamp() - min(item.timestamp for item in samples.values()).timestamp())
                self.metrics["safe"][node] = int(safe)
            except Exception as exc:
                # one line per failure, no traceback: the query text travels in the exception
                logging.error(f"evaluation failed for {node}: {exc!r}")
                self.metrics["query_errors"][node] += 1
                self.policies[node].invalidate()
                self._last_source_stamps[node] = {}
                self.metrics["safe"][node] = 0
                self.metrics["source_age_seconds"][node] = float("nan")
                if sensors:
                    self.metrics["nvme_temp_max"][node] = float("nan")
                else:
                    self.metrics["cpu_non_xmrig"][node] = float("nan")
        self.metrics["evaluations"] += 1
        self.ready = True
        return dict(self.metrics["safe"])


def render_metrics(controller):
    m = controller.metrics
    lines = [f'xmrig_guard_evaluations_total {m["evaluations"]}']
    for metric, values in (
        ("safe", m["safe"]),
        ("query_errors_total", m["query_errors"]),
        ("source_age_seconds", m["source_age_seconds"]),
        ("nvme_temp_max_celsius", m["nvme_temp_max"]),
        ("cpu_non_xmrig_percent", m["cpu_non_xmrig"]),
        ("rank", m["rank"]),
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
        # sleep to a deadline, not a flat interval: sleeping after the work made the true
        # period drift by the evaluation duration, stretching it against a fixed freshness budget
        deadline = time.monotonic() + EVALUATION_INTERVAL_SECONDS
        controller.evaluate()
        time.sleep(max(0, deadline - time.monotonic()))


if __name__ == "__main__":
    main()
