# Mock OpenAI-compatible backend: logs every request body to stderr, returns a canned reply.
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        keep = {k: v for k, v in body.items() if k not in ("messages", "input", "tools", "instructions")}
        print(f"MOCK {self.path} {json.dumps(keep, sort_keys=True)}", file=sys.stderr, flush=True)
        if "responses" in self.path:
            out = {"id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
                   "model": body.get("model"),
                   "output": [{"type": "message", "id": "m1", "status": "completed", "role": "assistant",
                               "content": [{"type": "output_text", "text": "18", "annotations": []}]}],
                   "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                             "output_tokens_details": {"reasoning_tokens": 0}}}
        else:
            out = {"id": "c1", "object": "chat.completion", "created": 0, "model": body.get("model"),
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": "18"},
                                "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


HTTPServer(("127.0.0.1", 18999), H).serve_forever()
