"""Forward MCP traffic to GitHub's hosted server, rewriting JSON Schema type arrays in tools/list.

ToolHive vMCP decodes each schema `type` as a single string and fails the whole backend on an array.
GitHub's issue_write emits ["string","number","boolean"] for issue_fields[].value, so every array is
replaced by its first non-null member. Only tools/list results are rewritten; tool call results pass through.
"""

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_URL"]
PORT = int(os.environ.get("PORT", "8080"))
# Hop-by-hop headers and anything whose value this proxy changes (the body is rewritten, so lengths and encodings no longer match).
DROP_HEADERS = {"host", "connection", "keep-alive", "content-length", "content-encoding", "transfer-encoding", "accept-encoding"}


def fix_types(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "type" and isinstance(value, list):
                node[key] = next((t for t in value if t != "null"), "null")
            else:
                fix_types(value)
    elif isinstance(node, list):
        for item in node:
            fix_types(item)


def fix_tools_list(doc):
    if isinstance(doc, dict) and "tools" in doc.get("result", {}):
        fix_types(doc["result"]["tools"])
    return doc


def rewrite(body: bytes, content_type: str) -> bytes:
    if "text/event-stream" in content_type:
        lines = []
        for line in body.decode().split("\n"):
            if line.startswith("data:"):
                try:
                    doc = fix_tools_list(json.loads(line[5:].strip()))
                except ValueError:
                    lines.append(line)
                    continue
                line = "data: " + json.dumps(doc, separators=(",", ":"))
            lines.append(line)
        return "\n".join(lines).encode()
    if "application/json" in content_type:
        return json.dumps(fix_tools_list(json.loads(body)), separators=(",", ":")).encode()
    return body


class Shim(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._forward(b"")

    def do_POST(self):
        self._forward(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def do_DELETE(self):
        self._forward(b"")

    def _forward(self, body: bytes):
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_HEADERS}
        request = urllib.request.Request(UPSTREAM, data=body or None, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as err:
            resp = err
        content_type = resp.headers.get("Content-Type", "")
        self.send_response(resp.status)
        for name, value in resp.headers.items():
            if name.lower() not in DROP_HEADERS and name.lower() != "content-type":
                self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        if self.command == "GET" and resp.status == 200:
            # The GET listen stream never ends, so relay it unbuffered and close when either side hangs up.
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while chunk := resp.read1(8192):
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        payload = rewrite(resp.read(), content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Shim).serve_forever()
