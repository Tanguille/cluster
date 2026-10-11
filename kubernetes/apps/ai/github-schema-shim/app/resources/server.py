"""Forward MCP traffic to GitHub's hosted server, refusing the 2026-07-28 discovery call.

ToolHive vMCP tries the 2026-07-28 protocol first. GitHub's tool output schemas have top-level
JSON Schema type arrays, which that path cannot decode, failing the whole backend. A 404 on server/discover
makes vMCP fall back to the 2025-11-25 protocol, which decodes the same tools. Everything else passes through.
"""

import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.githubcopilot.com/mcp/"
PORT = 8080
# Not forwarded in either direction: hop-by-hop headers, the length and encoding of a body that is
# buffered and re-sent, and Date/Server, which send_response adds itself.
DROP_HEADERS = {"host", "connection", "keep-alive", "content-length", "content-encoding",
                "transfer-encoding", "accept-encoding", "date", "server"}


class Shim(BaseHTTPRequestHandler):
    # Probes and MCP requests arrive constantly; the access log would only fill the pod's stderr.
    def log_message(self, *args):
        pass

    # The default HTTP/1.0 closes each connection after its response, which also ends the GET listen stream.
    def do_GET(self):
        if self.path == "/healthz":
            self._empty(200)
            return
        self._forward(b"")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("Mcp-Method") == "server/discover":
            self._empty(404)
            return
        self._forward(body)

    def do_DELETE(self):
        self._forward(b"")

    def _empty(self, status: int):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _forward(self, body: bytes):
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_HEADERS}
        request = urllib.request.Request(UPSTREAM, data=body or None, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as err:
            resp = err
        self.send_response(resp.status)
        for name, value in resp.headers.items():
            if name.lower() not in DROP_HEADERS:
                self.send_header(name, value)
        if self.command == "GET" and resp.status == 200:
            # Relay the listen stream as it arrives: buffering would hold it until upstream closes, which it never does.
            self.end_headers()
            while chunk := resp.read1(8192):
                self.wfile.write(chunk)
            return
        payload = resp.read()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Shim).serve_forever()
