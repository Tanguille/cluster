"""Kritika confidence tool adapter or Siren's advisory Alertmanager proxy."""
import hmac
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import d3_client
from d3_client import question

MAX_BODY = 65536
MAX_TEXT = 12000
MIN_PROBABILITY = .7
RISK = {"low": "Little consequential behavior change", "medium": "Routine reversible change",
        "high": "Breaking change to a shared dependency or access grant",
        "critical": "Irrecoverable outage, data loss, administrative access or secret exposure"}
SCORES = {"0": "Must not merge", "1": "Several P0s or devastating P0", "2": "P0 finding",
          "3": "P1 finding", "4": "No P0/P1 but unanswered concrete concern", "5": "No P0/P1 or uncovered concern"}


def confidence_tool(body):
    tools = body.get("tools")
    function = tools[0].get("function") if isinstance(tools, list) and len(tools) == 1 and isinstance(tools[0], dict) else None
    if not isinstance(function, dict) or function.get("name") != "confidence":
        raise ValueError("Only one confidence function tool is supported")
    # Our fixed output must satisfy the caller's schema, so pin only what it depends on.
    schema = function.get("parameters")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if (not isinstance(properties, dict) or set(properties) != {"score", "risk", "reason"}
            or properties["score"].get("type") != "integer" or properties["risk"].get("enum") != list(RISK)
            or not set(schema.get("required", [])) <= set(properties)):
        raise ValueError("Unsupported confidence schema")
    choice = body.get("tool_choice", "auto")
    if choice not in ("auto", "required") and choice != {"type": "function", "function": {"name": "confidence"}}:
        raise ValueError("Unsupported tool choice")


def verify(result, questions):
    if not isinstance(result, dict) or result.get("model") != "d3-nano":
        raise ValueError("Unexpected native model")
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Missing answers")
    for name, item in questions.items():
        d3_client.check_answer(answers[name], item["criteria"], .001)
    usage = result.get("usage", {})
    if type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0 or type(usage.get("output_tokens")) is not int or usage["output_tokens"] != 0:
        raise ValueError("Invalid usage")
    return result


class Native:
    def __init__(self, native_url="http://127.0.0.1:8100", timeout=30):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid gateway configuration")
        self.native_url, self.timeout = native_url.rstrip("/"), timeout
        self.native_slots = threading.BoundedSemaphore(2)

    def decide(self, state, questions, timeout=None):
        if not isinstance(state, str) or len(state) > MAX_TEXT:
            raise ValueError("State exceeds text limit; nothing was truncated")
        if not self.native_slots.acquire(blocking=False):
            raise ValueError("Decision queue full")
        try:
            return verify(d3_client.post(self.native_url + "/v1/systemone", state, questions, timeout or self.timeout, 1048576), questions)
        finally:
            self.native_slots.release()

    def selected(self, answer):
        choice = answer["choice"]
        probability = answer["probabilities"][choice]
        if probability < MIN_PROBABILITY:
            raise ValueError("Uncertain decision")
        return choice, probability


class Gateway(Native):
    """Kritika confidence adapter behind agentgateway."""

    def __init__(self, api_key, native_url="http://127.0.0.1:8100", timeout=30):
        if not api_key:
            raise ValueError("Invalid gateway configuration")
        super().__init__(native_url, timeout)
        self.api_key = api_key

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


class AlertGateway(Native):
    """Advisory D3 annotations on Siren's Alertmanager reads."""

    def __init__(self, native_url, alertmanager_url, timeout=30):
        super().__init__(native_url, timeout)
        self.alertmanager_url = alertmanager_url.rstrip("/")
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.alert_cache, self.pending, self.lock = {}, set(), threading.Lock()

    def enrich(self, alerts):
        """Annotate alerts in place; return whether anything was added."""
        changed = False
        for alert in alerts:
            # Fingerprint, not the alert body: updatedAt/endsAt change on every resend.
            key = alert.get("fingerprint") if isinstance(alert, dict) else None
            if not isinstance(key, str):
                continue
            with self.lock:
                cached = self.alert_cache.get(key)
                if cached and cached[0] > time.monotonic():
                    annotations = alert.setdefault("annotations", {})
                    if isinstance(annotations, dict):
                        for name, value in cached[1].items():
                            if name not in annotations:
                                annotations[name] = value
                                changed = True
                elif key not in self.pending and len(self.pending) < 16:
                    self.pending.add(key)
                    self.executor.submit(self.classify_alert, key, alert)
        return changed

    def classify_alert(self, key, alert):
        annotations = {}
        try:
            tasks = {"category": question("Classify this alert for advisory display only.", d3_client.ALERT_CATEGORIES),
                     "urgency": question("Classify urgency for advisory display only; retain original alert severity.", d3_client.ALERT_URGENCY)}
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


class ConfidenceHandler(Handler):
    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        if self.path == "/readyz":
            try:
                with urlopen(self.app.native_url + "/health", timeout=2) as response:
                    return self.reply(200 if response.status == 200 else 503, {"status": "ready"})
            except Exception:
                return self.reply(503, {"error": "Native worker unavailable"})
        self.reply(404, {"error": "Unknown endpoint"})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + self.app.api_key).encode()):
            return self.reply(401, {"error": "Unauthorized"})
        if self.path != "/v1/chat/completions":
            return self.reply(404, {"error": "Unknown endpoint"})
        try:
            body = json.loads(self.body())
            if not isinstance(body, dict) or body.get("model") != "d3-confidence" or body.get("stream", False) is not False:
                return self.reply(400, {"error": "Only nonstreaming d3-confidence is supported"})
            self.reply(200, self.app.confidence(body))
        except Exception:
            self.reply(503, {"error": "Decision unavailable; use configured fallback"})

    def do_DELETE(self):
        self.reply(405, {"error": "Unsupported method"})


class AlertHandler(Handler):
    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        self.proxy()

    def do_POST(self):
        self.proxy()

    do_DELETE = do_POST

    def proxy(self):
        app = self.app
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
                if isinstance(payload, list) and app.enrich(payload):
                    raw = json.dumps(payload).encode()
            self.reply(status, raw, content_type)
        except Exception:
            self.reply(502, {"error": "Alertmanager unavailable"})


def make_server(app, address):
    handler = AlertHandler if isinstance(app, AlertGateway) else ConfidenceHandler
    return ThreadingHTTPServer(address, type("Bound", (handler,), {"app": app}))


if __name__ == "__main__":
    native_url = os.getenv("D3_NATIVE_URL", "http://127.0.0.1:8100")
    alert_port = int(os.getenv("D3_ALERT_PORT", "0"))
    if alert_port:
        make_server(AlertGateway(native_url, os.environ["D3_ALERTMANAGER_URL"]), ("127.0.0.1", alert_port)).serve_forever()
    else:
        make_server(Gateway(os.getenv("LLM_API_KEY", ""), native_url), ("0.0.0.0", int(os.getenv("D3_GATEWAY_PORT", "8101")))).serve_forever()
