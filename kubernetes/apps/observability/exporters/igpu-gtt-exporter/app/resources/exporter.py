"""Per-pod DRM GTT exporter: sums drm-memory-gtt from /proc/*/fdinfo by pod uid (needs hostPID)."""
import glob
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

UNITS = {"": 1, "KiB": 1 << 10, "MiB": 1 << 20}  # drm_fdinfo_print_size units
POD_UID = re.compile(r"/pod([0-9a-f-]{36})/")  # cgroupfs driver, as on Talos


def parse_fdinfo(text):
    """Return (client id, gtt bytes) for a DRM fdinfo, or None."""
    fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    client, gtt = fields.get("drm-client-id"), fields.get("drm-memory-gtt")
    if client is None or gtt is None:
        return None
    value, _, unit = gtt.strip().partition(" ")
    return client.strip(), int(value) * UNITS[unit]


def pod_uid(cgroup_text):
    match = POD_UID.search(cgroup_text)
    return match.group(1) if match else None


def collect(proc="/proc"):
    """Bytes per pod uid; fds sharing one drm-client-id are one client, counted once."""
    clients = {}
    for fd in glob.glob(f"{proc}/[0-9]*/fd/*"):
        try:
            if not os.readlink(fd).startswith("/dev/dri/"):
                continue
            pid_dir = fd.rsplit("/fd/", 1)[0]
            with open(fd.replace("/fd/", "/fdinfo/")) as f:
                parsed = parse_fdinfo(f.read())
            with open(f"{pid_dir}/cgroup") as f:
                uid = pod_uid(f.read())
        except (OSError, ValueError, KeyError):
            continue  # process or fd went away mid-scan, or an unparseable fdinfo
        if parsed and uid:
            clients[parsed[0]] = (uid, parsed[1])
    totals = {}
    for uid, gtt in clients.values():
        totals[uid] = totals.get(uid, 0) + gtt
    return totals


class Handler(BaseHTTPRequestHandler):
    timeout = 10  # an idle client must not block the single-threaded server

    def do_GET(self):
        lines = ["# HELP pod_drm_memory_gtt_bytes DRM GTT memory held by the pod's processes.",
                 "# TYPE pod_drm_memory_gtt_bytes gauge"]
        lines += [f'pod_drm_memory_gtt_bytes{{pod_uid="{uid}"}} {b}' for uid, b in sorted(collect().items())]
        body = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def self_check():
    # Real control-3 sample: note the space before the tab after drm-memory-gtt.
    sample = "drm-driver:\tamdgpu\ndrm-client-id:\t24\ndrm-memory-gtt: \t666232 KiB\n"
    assert parse_fdinfo(sample) == ("24", 666232 * 1024)
    assert parse_fdinfo("drm-client-id:\t1\ndrm-memory-gtt:\t3 MiB\n") == ("1", 3 << 20)
    assert parse_fdinfo("pos:\t0\nflags:\t02\n") is None
    cg = "0::/kubepods/burstable/podd9e1bcd4-3c79-41e4-9714-19bc78cdf48a/7e63f4d5\n"
    assert pod_uid(cg) == "d9e1bcd4-3c79-41e4-9714-19bc78cdf48a"
    assert pod_uid("0::/system/runtime\n") is None
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        self_check()
    else:
        # single-threaded: overlapping scrapes queue instead of scanning /proc in parallel
        HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
