#!/usr/bin/env python3
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
LAST_SUCCESSFUL_SCRAPE = 0.0
DEFAULT_MAX_METRICS_BYTES = int(os.environ.get("LLAMA_MAX_METRICS_BYTES", "1048576"))


def escape_label_value(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def upstream_url(base_url, model):
    query = urllib.parse.urlencode({"model": model, "autoload": "false"})
    return f"{base_url.rstrip('/')}/metrics?{query}"


def scrape_upstream(base_url, model, timeout, max_bytes=DEFAULT_MAX_METRICS_BYTES, urlopen=urllib.request.urlopen):
    url = upstream_url(base_url, model)
    try:
        with urlopen(url, timeout=timeout) as response:
            status = response.getcode()
            body = response.read(max_bytes + 1).decode("utf-8", "replace")
            if len(body.encode("utf-8")) > max_bytes:
                return 0, ""
            return status, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return 0, ""


def state_metrics(model, loaded, upstream_up, upstream_status, last_success):
    model_label = escape_label_value(model)
    loaded_value = 1 if loaded else 0
    upstream_value = 1 if upstream_up else 0
    return "\n".join(
        [
            "# HELP llama_model_loaded Whether this scrape collected live llama.cpp metrics for the model without autoloading.",
            "# TYPE llama_model_loaded gauge",
            f'llama_model_loaded{{model="{model_label}"}} {loaded_value}',
            "# HELP llama_upstream_metrics_up Whether the upstream llama.cpp metrics request succeeded.",
            "# TYPE llama_upstream_metrics_up gauge",
            f'llama_upstream_metrics_up{{model="{model_label}"}} {upstream_value}',
            "# HELP llama_exporter_last_successful_scrape_timestamp_seconds Unix timestamp of the last successful upstream llama.cpp metrics scrape.",
            "# TYPE llama_exporter_last_successful_scrape_timestamp_seconds gauge",
            f"llama_exporter_last_successful_scrape_timestamp_seconds {last_success:.3f}",
            "# HELP llama_exporter_upstream_http_status HTTP status from the upstream llama.cpp metrics request, or 0 when no HTTP status was available.",
            "# TYPE llama_exporter_upstream_http_status gauge",
            f'llama_exporter_upstream_http_status{{model="{model_label}"}} {upstream_status}',
            "",
        ]
    )


def build_metrics_response(
    base_url,
    model,
    timeout,
    max_bytes=DEFAULT_MAX_METRICS_BYTES,
    urlopen=urllib.request.urlopen,
    now=time.time,
    previous_success=None,
):
    global LAST_SUCCESSFUL_SCRAPE

    if previous_success is None:
        previous_success = LAST_SUCCESSFUL_SCRAPE

    status, upstream_body = scrape_upstream(base_url, model, timeout, max_bytes=max_bytes, urlopen=urlopen)
    if status == 200 and upstream_body.strip():
        LAST_SUCCESSFUL_SCRAPE = now()
        state = state_metrics(
            model=model,
            loaded=True,
            upstream_up=True,
            upstream_status=status,
            last_success=LAST_SUCCESSFUL_SCRAPE,
        )
        return 200, CONTENT_TYPE, state + upstream_body.rstrip() + "\n"

    state = state_metrics(
        model=model,
        loaded=False,
        upstream_up=False,
        upstream_status=status,
        last_success=previous_success,
    )
    return 200, CONTENT_TYPE, state


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return

        if self.path.split("?", 1)[0] != "/metrics":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"not found\n")
            return

        status, content_type, body = build_metrics_response(
            base_url=os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080"),
            model=os.environ.get("LLAMA_MODEL", "qwen-3.6"),
            timeout=float(os.environ.get("LLAMA_UPSTREAM_TIMEOUT", "5")),
        )
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def main():
    host = os.environ.get("LLAMA_EXPORTER_HOST", "0.0.0.0")
    port = int(os.environ.get("LLAMA_EXPORTER_PORT", "9108"))
    server = ThreadingHTTPServer((host, port), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
