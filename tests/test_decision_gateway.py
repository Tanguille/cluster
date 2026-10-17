"""Gateway trust boundaries, exercised over HTTP with a fake native worker."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PATH = Path(__file__).parents[1] / "kubernetes/components/d3-gateway/resources/decision_gateway.py"
spec = importlib.util.spec_from_file_location("decision_gateway", PATH)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class Fake(BaseHTTPRequestHandler):
    calls = []
    silences = []
    mode = "ok"
    alerts = [{"fingerprint": "abc", "labels": {"severity": "critical"},
               "annotations": {"summary": "Disk full"}, "status": {"state": "active"}}]

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(self.alerts).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/v2/silences":
            self.silences.append(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"silenceID":"created"}')
            return
        self.calls.append(body)
        if self.mode == "slow":
            time.sleep(.15)
        answers = {}
        for name, question in body["questions"].items():
            keys = list(question.get("criteria", {"false": "", "true": ""}))
            winner = "5" if name == "score" else keys[0]
            probabilities = {k: .9 if k == winner else .1 / (len(keys)-1) for k in keys}
            answers[name] = {"type": "choice", "choice": winner,
                             "probabilities": probabilities, "input_coverage": "complete"}
            if self.mode == "invalid":
                answers[name]["probabilities"][winner] = float("nan")
            if self.mode == "unknown":
                answers[name]["choice"] = "unlisted"
            if self.mode == "clipped":
                answers[name].pop("input_coverage")
            if self.mode == "sum":
                answers[name]["probabilities"] = {k: .9 for k in keys}
            if self.mode == "extra":
                answers[name]["probabilities"]["extra"] = .01
            if self.mode == "uncertain":
                answers[name]["probabilities"] = {k: 1/len(keys) for k in keys}
        result = {"model": "d3-nano", "answers": answers,
                  "usage": {"input_tokens": 42, "output_tokens": 0}}
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except BrokenPipeError:
            pass


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.native.serve_forever, daemon=True).start()
        cls.app = gateway.Gateway("secret", f"http://127.0.0.1:{cls.native.server_port}", timeout=.05)
        cls.server = gateway.make_server(cls.app, ("127.0.0.1", 0))
        cls.alert_app = gateway.Gateway(native_url=f"http://127.0.0.1:{cls.native.server_port}", timeout=.05, alerts=True)
        cls.alert_server = gateway.make_server(cls.alert_app, ("127.0.0.1", 0))
        cls.alert_app.alertmanager_url = f"http://127.0.0.1:{cls.native.server_port}"
        for server in (cls.server, cls.alert_server):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.alert_server, cls.native):
            server.shutdown()
            server.server_close()
        cls.app.executor.shutdown(wait=True)
        cls.alert_app.executor.shutdown(wait=True)

    def setUp(self):
        Fake.mode = "ok"
        Fake.calls.clear()
        Fake.silences.clear()
        with self.alert_app.lock:
            self.alert_app.alert_cache.clear()

    def request(self, body, path="/v1/chat/completions", token="secret", server=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"http://127.0.0.1:{(server or self.server).server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def confidence(self):
        return {"model": "d3-confidence", "messages": [
            {"role": "system", "content": "Exact rubric"},
            {"role": "user", "content": "Review and diff"}]}

    def kritika_confidence(self):
        # Source: home-operations/kritika tag 0.0.75, model/structured.go,
        # model/openai.go and review/confidence.go (not response_format).
        schema = {"type": "object", "properties": {
            "score": {"type": "integer", "description": "The confidence score, a whole number from 0 to 5."},
            "risk": {"type": "string", "description": "The risk of the change.",
                     "enum": ["low", "medium", "high", "critical"]},
            "reason": {"type": "string", "description": "The reason, as the instructions say: one or two plain sentences, no markdown."}},
            "required": ["score", "risk", "reason"]}
        body = self.confidence()
        body["messages"][1]["content"] += "\n\nAnswer by calling the confidence tool, and call nothing else; its input is your answer."
        return body | {"max_completion_tokens": 4096, "tool_choice": "auto",
                       "tools": [{"type": "function", "function": {"name": "confidence", "parameters": schema}}]}

    def test_auth_before_native_call(self):
        for token in (None, "wrong"):
            self.assertEqual(self.request(self.kritika_confidence(), token=token)[0], 401)
        self.assertEqual(Fake.calls, [])

    def test_removed_generic_apis_are_rejected(self):
        self.assertEqual(self.request({}, "/v1/systemone")[0], 404)
        self.assertEqual(self.request(self.confidence())[0], 503)
        self.assertEqual(Fake.calls, [])

    def test_kritika_pinned_structured_tool_call_contract(self):
        for choice in ("auto", "required", {"type": "function", "function": {"name": "confidence"}}):
            body = self.kritika_confidence() | {"tool_choice": choice}
            status, result = self.request(body, "/v1/chat/completions")
            self.assertEqual(status, 200)
            response = result["choices"][0]
            self.assertEqual(response["finish_reason"], "tool_calls")
            message = response["message"]
            self.assertIsNone(message["content"])
            self.assertEqual(len(message["tool_calls"]), 1)
            call = message["tool_calls"][0]
            self.assertEqual(call["type"], "function")
            self.assertTrue(call["id"])
            self.assertEqual(call["function"]["name"], "confidence")
            # Structured.Complete reads only these arguments, never content.
            arguments = json.loads(call["function"]["arguments"])
            self.assertEqual(set(arguments), {"score", "risk", "reason"})
            self.assertEqual((arguments["score"], arguments["risk"]), (5, "low"))
            self.assertIn("uncalibrated", arguments["reason"])
            self.assertEqual(result["usage"], {"prompt_tokens": 42, "completion_tokens": 0, "total_tokens": 42})
            self.assertIn("Exact rubric", Fake.calls[-1]["state"])
            self.assertIn("Review and diff", Fake.calls[-1]["state"])
            self.assertTrue(all(q["require_full_input"] for q in Fake.calls[-1]["questions"].values()))

    def test_confidence_rejects_arbitrary_tools_and_changed_tool_schema(self):
        bodies = []
        body = self.kritika_confidence()
        body["tools"][0]["function"]["name"] = "execute_shell"
        bodies.append(body)
        body = self.kritika_confidence()
        body["tools"][0]["function"]["parameters"]["properties"]["score"]["type"] = "string"
        bodies.append(body)
        body = self.kritika_confidence()
        body["tools"][0]["function"]["parameters"]["required"].append("extra")
        bodies.append(body)
        bodies.append(self.kritika_confidence() | {"tool_choice": "none"})
        bodies.append(self.kritika_confidence() | {"tool_choice": {"type": "function", "function": {"name": "execute_shell"}}})
        for body in bodies:
            self.assertEqual(self.request(body, "/v1/chat/completions")[0], 503)
        self.assertEqual(Fake.calls, [])

    def test_confidence_fails_closed_for_uncertain_invalid_clipped_and_timeout(self):
        for mode in ("invalid", "unknown", "clipped", "uncertain", "sum", "extra", "slow"):
            Fake.mode = mode
            self.assertEqual(self.request(self.kritika_confidence())[0], 503, mode)

    def test_confidence_overlarge_and_generic_chat_rejected(self):
        body = self.kritika_confidence()
        body["messages"][1]["content"] = "x" * 12001
        self.assertEqual(self.request(body)[0], 503)
        body["messages"][1]["content"] = "x" * 66000
        self.assertEqual(self.request(body)[0], 503)
        body = self.kritika_confidence()
        body["messages"][1]["content"] = [{"type": "image_url", "image_url": {"url": "http://x"}}]
        self.assertEqual(self.request(body)[0], 503)
        self.assertEqual(self.request(self.kritika_confidence() | {"model": "other"})[0], 400)
        self.assertEqual(self.request(self.kritika_confidence() | {"stream": True})[0], 400)
        self.assertEqual(Fake.calls, [])

    def test_alert_proxy_preserves_original_and_enriches_later(self):
        original = copy.deepcopy(Fake.alerts)
        started = time.monotonic()
        status, alerts = self.request(None, "/api/v2/alerts?active=true", token=None, server=self.alert_server)
        self.assertEqual(status, 200)
        self.assertEqual(alerts, original)
        self.assertLess(time.monotonic()-started, .15)
        deadline = time.monotonic()+1
        while not self.alert_app.alert_cache and time.monotonic() < deadline:
            time.sleep(.01)
        _, enriched = self.request(None, "/api/v2/alerts", token=None, server=self.alert_server)
        self.assertEqual(enriched[0]["labels"], original[0]["labels"])
        self.assertEqual(enriched[0]["annotations"]["summary"], "Disk full")
        self.assertIn("d3_category", enriched[0]["annotations"])
        self.assertEqual(Fake.alerts, original)
        self.assertEqual(self.request(None, "/arbitrary", token=None, server=self.alert_server)[0], 404)

    def test_alert_failure_keeps_original_and_silence_payload_is_forwarded(self):
        Fake.mode = "invalid"
        _, alerts = self.request(None, "/api/v2/alerts", token=None, server=self.alert_server)
        self.assertEqual(alerts, Fake.alerts)
        deadline = time.monotonic()+1
        while not self.alert_app.alert_cache and time.monotonic() < deadline:
            time.sleep(.01)
        _, alerts = self.request(None, "/api/v2/alerts", token=None, server=self.alert_server)
        self.assertEqual(alerts, Fake.alerts)
        body = {"matchers": [{"name": "alertname", "value": "test", "isRegex": False}],
                "createdBy": "test", "comment": "test"}
        status, result = self.request(body, "/api/v2/silences", token=None, server=self.alert_server)
        self.assertEqual((status, result), (200, {"silenceID": "created"}))
        self.assertEqual(Fake.silences, [body])

    def test_alert_proxy_rejects_absolute_and_traversal_paths(self):
        for path in ("//attacker.invalid/api/v2/alerts", "/api/v2/silence/..", "/api/v2/silence/%2e%2e"):
            self.assertEqual(self.request(None, path, token=None, server=self.alert_server)[0], 404)

    def test_alert_native_outage_returns_originals(self):
        native_url = self.alert_app.native_url
        self.alert_app.native_url = "http://127.0.0.1:0"
        try:
            for _ in range(2):
                status, alerts = self.request(None, "/api/v2/alerts", token=None, server=self.alert_server)
                self.assertEqual((status, alerts), (200, Fake.alerts))
                deadline = time.monotonic()+1
                while self.alert_app.pending and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertEqual(Fake.calls, [])
            self.assertEqual(self.request(None, "/healthz", token=None, server=self.alert_server)[0], 200)
            self.assertEqual(self.request(self.kritika_confidence(), server=self.alert_server)[0], 404)
        finally:
            self.alert_app.native_url = native_url

    def test_central_mode_requires_key_but_alert_mode_does_not(self):
        with self.assertRaises(ValueError):
            gateway.Gateway()

    def test_alert_process_has_only_alert_listener_and_needs_no_key(self):
        with socket.socket() as alert_socket, socket.socket() as central_socket:
            alert_socket.bind(("127.0.0.1", 0))
            central_socket.bind(("127.0.0.1", 0))
            alert_port, central_port = alert_socket.getsockname()[1], central_socket.getsockname()[1]
        environment = os.environ | {"D3_ALERT_PORT": str(alert_port), "D3_GATEWAY_PORT": str(central_port),
                                    "D3_BIND_HOST": "127.0.0.1", "D3_NATIVE_URL": "http://127.0.0.1:0"}
        environment.pop("LITELLM_API_KEY", None)
        process = subprocess.Popen([sys.executable, str(PATH)], env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+2
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{alert_port}/healthz", timeout=.2) as response:
                        self.assertEqual(response.status, 200)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("Alert-only process did not become healthy without an API key")
                    time.sleep(.01)
            with self.assertRaises(OSError):
                urlopen(f"http://127.0.0.1:{central_port}/healthz", timeout=.2)
        finally:
            process.terminate()
            process.communicate(timeout=2)

    def test_bind_host_accepts_only_localhost_or_ip_addresses(self):
        for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            self.assertEqual(gateway.bind_host(host), host)
        for host in ("example.com", "127.0.0.1:8101", "http://127.0.0.1", ""):
            with self.assertRaises(ValueError):
                gateway.bind_host(host)


if __name__ == "__main__":
    unittest.main()
