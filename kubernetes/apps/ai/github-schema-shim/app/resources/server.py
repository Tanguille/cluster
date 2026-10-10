"""Forward MCP traffic to GitHub's hosted server, rewriting JSON Schema type arrays in tools/list.

ToolHive vMCP decodes each schema `type` as a single string and fails the whole backend on an array.
GitHub's issue_write emits ["string","number","boolean"] for issue_fields[].value, so every array is
replaced by its first non-null member. Only tools/list results are rewritten; tool call results pass through.
"""

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.githubcopilot.com/mcp/"
PORT = 8080
# Not forwarded in either direction: hop-by-hop headers, headers for a body this proxy rewrites
# (length, encoding), and Date/Server, which send_response adds itself.
DROP_HEADERS = {"host", "connection", "keep-alive", "content-length", "content-encoding",
                "transfer-encoding", "accept-encoding", "date", "server"}


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


def rewrite_sse_line(line: str) -> str:
    if not line.startswith("data:"):
        return line
    try:
        doc = json.loads(line[5:])
    except ValueError:
        return line
    return "data: " + json.dumps(fix_tools_list(doc))


def rewrite(body: bytes, content_type: str) -> bytes:
    if "text/event-stream" in content_type:
        return "\n".join(rewrite_sse_line(line) for line in body.decode().split("\n")).encode()
    if "application/json" in content_type:
        return json.dumps(fix_tools_list(json.loads(body))).encode()
    return body


class Shim(BaseHTTPRequestHandler):
    # The default HTTP/1.0 closes each connection after its response, which also ends the GET listen stream.
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
        payload = rewrite(resp.read(), resp.headers.get("Content-Type", ""))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Shim).serve_forever()
