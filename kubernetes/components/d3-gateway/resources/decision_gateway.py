"""Kritika confidence tool adapter or Siren's advisory Alertmanager proxy."""
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BODY = 65536
MAX_TEXT = 12000
RISK = {"low": "Little consequential behavior change", "medium": "Routine reversible change",
        "high": "Breaking change to a shared dependency or access grant",
        "critical": "Irrecoverable outage, data loss, administrative access or secret exposure"}
SCORES = {"0": "Must not merge", "1": "Several P0s or devastating P0", "2": "P0 finding",
          "3": "P1 finding", "4": "No P0/P1 but unanswered concrete concern", "5": "No P0/P1 or uncovered concern"}


def question(instructions, criteria):
    return {"type": "choice", "instructions": instructions, "criteria": criteria, "require_full_input": True}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def bind_host(value):
    if value != "localhost":
        ipaddress.ip_address(value)
    return value


def confidence_tool(body):
    tools = body.get("tools")
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict) or set(tools[0]) != {"type", "function"} or tools[0]["type"] != "function":
        raise ValueError("Only one confidence function tool is supported")
    function = tools[0]["function"]
    if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"} or function.get("name") != "confidence":
        raise ValueError("Unsupported tool")
    schema = function.get("parameters", {})
    if not isinstance(schema, dict) or set(schema) - {"type", "properties", "required", "additionalProperties"} or schema.get("type") != "object" or schema.get("additionalProperties", False) is not False:
        raise ValueError("Unsupported confidence schema")
    properties, required = schema.get("properties"), schema.get("required")
    if not isinstance(properties, dict) or set(properties) != {"score", "risk", "reason"} or not isinstance(required, list) or len(required) != 3 or set(required) != set(properties):
        raise ValueError("Unsupported confidence fields")
    for name, expected in (("score", "integer"), ("risk", "string"), ("reason", "string")):
        field = properties[name]
        allowed = {"type", "description", "enum"} if name == "risk" else {"type", "description"}
        if not isinstance(field, dict) or field.get("type") != expected or set(field) - allowed:
            raise ValueError("Unsupported confidence field type")
    if properties["risk"].get("enum") != list(RISK):
        raise ValueError("Unsupported risk levels")
    choice = body.get("tool_choice", "auto")
    if choice not in ("auto", "required") and choice != {"type": "function", "function": {"name": "confidence"}}:
        raise ValueError("Unsupported tool choice")
    if "parallel_tool_calls" in body and type(body["parallel_tool_calls"]) is not bool:
        raise ValueError("Invalid parallel_tool_calls")


def verify(result, questions):
    if not isinstance(result, dict) or result.get("model") != "d3-nano":
        raise ValueError("Unexpected native model")
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Missing answers")
    for name, item in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("input_coverage") != "complete" or "error" in answer:
            raise ValueError("Incomplete or failed decision")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(item["criteria"]) or any(not finite(p) or not 0 <= p <= 1 for p in probabilities.values()) or not math.isclose(sum(probabilities.values()), 1, abs_tol=.001):
            raise ValueError("Invalid probabilities")
        choice = answer.get("choice")
        if choice not in probabilities or probabilities[choice] != max(probabilities.values()):
            raise ValueError("Invalid choice")
    usage = result.get("usage", {})
    if type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0 or type(usage.get("output_tokens")) is not int or usage["output_tokens"] != 0:
        raise ValueError("Invalid usage")
    return result


class Gateway:
    def __init__(self, api_key="", native_url="http://127.0.0.1:8100", timeout=30, min_probability=.7, alerts=False):
        if (not alerts and not api_key) or not 0 < timeout <= 30 or not .5 < min_probability <= 1:
            raise ValueError("Invalid gateway configuration")
        self.alerts = alerts
        self.api_key, self.native_url = api_key, native_url.rstrip("/")
        self.timeout, self.min_probability = timeout, min_probability
        self.native_slots = threading.BoundedSemaphore(2)
        self.alertmanager_url = "http://vmalertmanager-victoria-metrics.observability.svc.cluster.local:9093"
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.alert_slots = threading.BoundedSemaphore(16)
        self.alert_cache, self.pending, self.lock = {}, set(), threading.Lock()

    def decide(self, state, questions, timeout=None):
        if not isinstance(state, str) or len(state) > MAX_TEXT:
            raise ValueError("State exceeds text limit; nothing was truncated")
        if not self.native_slots.acquire(blocking=False):
            raise ValueError("Decision queue full")
        try:
            request = Request(self.native_url + "/v1/systemone", data=json.dumps({"state": state, "questions": questions}).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError("Oversized native response")
            return verify(json.loads(raw), questions)
        finally:
            self.native_slots.release()

    def selected(self, answer):
        choice = answer["choice"]
        probability = answer["probabilities"][choice]
        if probability < self.min_probability:
            raise ValueError("Uncertain decision")
        return choice, probability

    def confidence(self, body):
        if set(body) - {"model", "messages", "stream", "max_tokens", "max_completion_tokens",
                        "temperature", "top_p", "seed", "user", "metadata", "reasoning_effort", "tools", "tool_choice", "parallel_tool_calls"}:
            raise ValueError("Unsupported confidence request fields")
        confidence_tool(body)
        messages = body.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
            raise ValueError("Expected text messages")
        for message in messages:
            if not isinstance(message, dict) or set(message) - {"role", "content", "name"} or message.get("role") not in {"system", "developer", "user", "assistant"} or not isinstance(message.get("content"), str):
                raise ValueError("Only text messages are supported")
        state = json.dumps(messages, ensure_ascii=False)
        tasks = {"score": question("Apply the supplied Kritika confidence rubric to the review. Select its integer confidence score.", SCORES),
                 "risk": question("Apply the supplied Kritika risk rubric to the change. Select its risk level independently of score.", RISK)}
        result = self.decide(state, tasks)
        score, sp = self.selected(result["answers"]["score"])
        risk, rp = self.selected(result["answers"]["risk"])
        content = {"score": int(score), "risk": risk,
                   "reason": f"D3 selected score {score} (probability {sp:.3f}) and risk {risk} (probability {rp:.3f}); these are uncalibrated classifier probabilities, not a generated review rationale."}
        tokens = result["usage"]["input_tokens"]
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_" + uuid.uuid4().hex,
                   "type": "function", "function": {"name": "confidence", "arguments": json.dumps(content)}}]}
        return {"id": "chatcmpl-d3-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()), "model": "d3-confidence",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens}}

    def enrich(self, alerts):
        output = copy.deepcopy(alerts)
        for alert in output:
            if not isinstance(alert, dict):
                continue
            key = hashlib.sha256(json.dumps(alert, sort_keys=True).encode()).hexdigest()
            with self.lock:
                cached = self.alert_cache.get(key)
                if cached and cached[0] > time.monotonic():
                    annotations = alert.setdefault("annotations", {})
                    if isinstance(annotations, dict):
                        for name, value in cached[1].items():
                            annotations.setdefault(name, value)
                elif key not in self.pending and self.alert_slots.acquire(blocking=False):
                    self.pending.add(key)
                    self.executor.submit(self.classify_alert, key, copy.deepcopy(alert))
        return output

    def classify_alert(self, key, alert):
        annotations = {}
        try:
            categories = {"capacity": "Resource capacity", "availability": "Service unavailable", "security": "Security concern", "configuration": "Configuration or reconciliation", "other": "Other"}
            urgency = {"immediate": "Needs attention now", "soon": "Needs attention soon", "routine": "Routine attention"}
            tasks = {"category": question("Classify this alert for advisory display only.", categories),
                     "urgency": question("Classify urgency for advisory display only; retain original alert severity.", urgency)}
            result = self.decide(json.dumps(alert, ensure_ascii=False), tasks, timeout=min(3, self.timeout))
            annotations = {"d3_" + name: self.selected(result["answers"][name])[0] for name in tasks}
        except Exception:
            pass  # Advisory failures never affect alert delivery.
        finally:
            with self.lock:
                if len(self.alert_cache) >= 128:
                    self.alert_cache.pop(next(iter(self.alert_cache)))
                self.alert_cache[key] = (time.monotonic()+60, annotations)
                self.pending.discard(key)
            self.alert_slots.release()


def make_server(app, address):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Requests contain private review and memory data.

        def reply(self, status, body, content_type="application/json"):
            raw = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def body(self):
            if self.headers.get_content_type() != "application/json" or "Transfer-Encoding" in self.headers:
                raise ValueError("Expected unchunked JSON")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise OverflowError("Request exceeds body limit")
            self.connection.settimeout(5)
            return self.rfile.read(size)

        def do_GET(self):
            if self.path == "/healthz":
                return self.reply(200, {"status": "ok"})
            if app.alerts:
                return self.proxy()
            if self.path == "/readyz":
                try:
                    with urlopen(app.native_url + "/health", timeout=2) as response:
                        return self.reply(200 if response.status == 200 else 503, {"status": "ready"})
                except Exception:
                    return self.reply(503, {"error": "Native worker unavailable"})
            self.reply(404, {"error": "Unknown endpoint"})

        def do_POST(self):
            if app.alerts:
                return self.proxy()
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + app.api_key).encode()):
                return self.reply(401, {"error": "Unauthorized"})
            if self.path != "/v1/chat/completions":
                return self.reply(404, {"error": "Unknown endpoint"})
            try:
                body = json.loads(self.body())
                if not isinstance(body, dict) or body.get("model") != "d3-confidence" or body.get("stream", False) is not False:
                    return self.reply(400, {"error": "Only nonstreaming d3-confidence is supported"})
                self.reply(200, app.confidence(body))
            except Exception:
                self.reply(503, {"error": "Decision unavailable; use configured fallback"})

        def do_DELETE(self):
            if app.alerts:
                return self.proxy()
            self.reply(405, {"error": "Unsupported method"})

        def proxy(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if parsed.scheme or parsed.netloc or parsed.fragment:
                return self.reply(404, {"error": "Unsupported Alertmanager route"})
            reads = {"/api/v2/alerts", "/api/v2/alerts/groups", "/api/v2/status", "/api/v2/receivers", "/api/v2/silences"}
            silence = re.fullmatch(r"/api/v2/silence/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", path)
            if not ((self.command == "GET" and (path in reads or silence)) or
                    (self.command == "POST" and path == "/api/v2/silences") or (self.command == "DELETE" and silence)):
                return self.reply(404, {"error": "Unsupported Alertmanager route"})
            try:
                data = self.body() if self.command == "POST" else None
                request = Request(app.alertmanager_url + self.path, data=data, method=self.command, headers={"Content-Type": "application/json"})
                try:
                    response = urlopen(request, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    raw, status = response.read(4194305), response.status
                    content_type = response.headers.get("Content-Type", "application/json")
                if len(raw) > 4194304:
                    raise ValueError("Oversized Alertmanager response")
                if self.command == "GET" and path == "/api/v2/alerts" and status == 200:
                    payload = json.loads(raw)
                    if isinstance(payload, list):
                        raw = json.dumps(app.enrich(payload)).encode()
                self.reply(status, raw, content_type)
            except Exception:
                self.reply(502, {"error": "Alertmanager unavailable"})
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    return Server(address, Handler)


if __name__ == "__main__":
    alert_port = int(os.getenv("D3_ALERT_PORT", "0"))
    app = Gateway(os.getenv("LITELLM_API_KEY", ""), os.getenv("D3_NATIVE_URL", "http://127.0.0.1:8100"),
                  float(os.getenv("D3_TIMEOUT_SECONDS", "30")), float(os.getenv("D3_MIN_PROBABILITY", ".7")), alerts=bool(alert_port))
    host = bind_host(os.getenv("D3_BIND_HOST", "0.0.0.0"))
    make_server(app, (host, alert_port or int(os.getenv("D3_GATEWAY_PORT", "8101")))).serve_forever()
