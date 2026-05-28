import importlib.util
import io
import socket
import unittest
import urllib.error
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("llama-metrics-gate.py")


def load_module():
    spec = importlib.util.spec_from_file_location("llama_metrics_gate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            return self._body
        return self._body[:size]

    @property
    def headers(self):
        return {}


class MetricsGateTests(unittest.TestCase):
    def test_success_passes_through_llama_metrics_and_state(self):
        gate = load_module()
        calls = []

        def fake_urlopen(url, timeout):
            calls.append((url, timeout))
            return FakeResponse(200, "llamacpp:requests_processing 1\n")

        status, content_type, body = gate.build_metrics_response(
            base_url="http://127.0.0.1:8080",
            model="qwen-3.6",
            timeout=5.0,
            urlopen=fake_urlopen,
            now=lambda: 1234.0,
            previous_success=0.0,
        )

        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertIn("llamacpp:requests_processing 1", body)
        self.assertIn('llama_model_loaded{model="qwen-3.6"} 1', body)
        self.assertIn('llama_upstream_metrics_up{model="qwen-3.6"} 1', body)
        self.assertIn('llama_exporter_upstream_http_status{model="qwen-3.6"} 200', body)
        self.assertIn("llama_exporter_last_successful_scrape_timestamp_seconds 1234.000", body)
        self.assertEqual(calls[0][1], 5.0)
        self.assertIn("model=qwen-3.6", calls[0][0])
        self.assertIn("autoload=false", calls[0][0])

    def test_http_400_returns_successful_cold_state_metrics(self):
        gate = load_module()

        def fake_urlopen(url, timeout):
            raise urllib.error.HTTPError(
                url=url,
                code=400,
                msg="model is not loaded",
                hdrs={},
                fp=io.BytesIO(b"model is not loaded"),
            )

        status, content_type, body = gate.build_metrics_response(
            base_url="http://127.0.0.1:8080",
            model="qwen-3.6",
            timeout=5.0,
            urlopen=fake_urlopen,
            now=lambda: 2000.0,
            previous_success=1111.0,
        )

        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertNotIn("llamacpp:", body)
        self.assertIn('llama_model_loaded{model="qwen-3.6"} 0', body)
        self.assertIn('llama_upstream_metrics_up{model="qwen-3.6"} 0', body)
        self.assertIn('llama_exporter_upstream_http_status{model="qwen-3.6"} 400', body)
        self.assertIn("llama_exporter_last_successful_scrape_timestamp_seconds 1111.000", body)

    def test_large_upstream_body_returns_successful_cold_state_metrics_without_full_read(self):
        gate = load_module()
        gate.LAST_SUCCESSFUL_SCRAPE = 0.0

        large_body = "x" * (gate.DEFAULT_MAX_METRICS_BYTES + 100)
        response = FakeResponse(200, large_body)

        def fake_urlopen(url, timeout):
            return response

        status, content_type, body = gate.build_metrics_response(
            base_url="http://127.0.0.1:8080",
            model="qwen-3.6",
            timeout=5.0,
            urlopen=fake_urlopen,
            now=lambda: 4000.0,
            previous_success=0.0,
        )

        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertIn('llama_model_loaded{model="qwen-3.6"} 0', body)
        self.assertIn('llama_upstream_metrics_up{model="qwen-3.6"} 0', body)
        self.assertIn('llama_exporter_upstream_http_status{model="qwen-3.6"} 0', body)
        self.assertNotIn("x" * 100, body)
        self.assertEqual(response.read_sizes, [gate.DEFAULT_MAX_METRICS_BYTES + 1])

    def test_timeout_returns_successful_state_metrics_with_status_zero(self):
        gate = load_module()

        def fake_urlopen(url, timeout):
            raise socket.timeout("timed out")

        status, content_type, body = gate.build_metrics_response(
            base_url="http://127.0.0.1:8080",
            model="qwen-3.6",
            timeout=5.0,
            urlopen=fake_urlopen,
            now=lambda: 3000.0,
            previous_success=0.0,
        )

        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertIn('llama_model_loaded{model="qwen-3.6"} 0', body)
        self.assertIn('llama_upstream_metrics_up{model="qwen-3.6"} 0', body)
        self.assertIn('llama_exporter_upstream_http_status{model="qwen-3.6"} 0', body)
        self.assertIn("llama_exporter_last_successful_scrape_timestamp_seconds 0.000", body)


if __name__ == "__main__":
    unittest.main()
