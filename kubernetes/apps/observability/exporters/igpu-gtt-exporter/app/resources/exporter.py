"""Per-pod DRM GTT exporter: sums drm-memory-gtt from DRM clients' fdinfo by pod uid (needs hostPID and debugfs)."""
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

lazy import tempfile  # self-check only; the server never loads it

UNITS = frozendict({"": 1, "KiB": 1 << 10, "MiB": 1 << 20})  # drm_fdinfo_print_size units
POD_UID = re.compile(r"/pod([0-9a-f-]{36})/")  # cgroupfs driver, as on Talos


def parse_fdinfo(text):
    """Return ((device, client id), gtt bytes) for a DRM fdinfo, or None."""
    fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    client, gtt = fields.get("drm-client-id"), fields.get("drm-memory-gtt")
    if client is None or gtt is None:
        return None
    value, _, unit = gtt.strip().partition(" ")
    # client ids are unique per device only, so key on the PCI slot too
    device = fields.get("drm-pdev", "").strip()
    return (device, client.strip()), int(value) * UNITS[unit]


def pod_uid(cgroup_text):
    match = POD_UID.search(cgroup_text)
    return match.group(1) if match else None


def drm_client_pids(dri):
    """Tgids holding a DRM file open, from each minor's debugfs clients table."""
    pids = set()
    for minor in os.listdir(dri):
        try:
            with open(f"{dri}/{minor}/clients") as f:
                header, *rows = f.read().splitlines()
        except OSError:
            continue  # bridges and other non-minor entries have no clients table
        cols = header.split()
        # command is the only column that can hold spaces, so split from the right
        pids.update(row.rsplit(None, len(cols) - 1)[cols.index("tgid")] for row in rows if row.strip())
    return pids


def collect(proc="/proc", dri="/sys/kernel/debug/dri"):
    """Bytes per pod uid; fds sharing one device and drm-client-id are one client, counted once."""
    clients = {}
    # Walking every /proc fd took 10s+ on control-2 (~540k fds, almost all gVisor sandboxes);
    # debugfs names the DRM clients, so only their few fds are read.
    for pid in drm_client_pids(dri):
        try:
            fds = os.scandir(f"{proc}/{pid}/fd")
        except OSError:
            continue  # process exited mid-scan
        uid = None  # cgroup is read once per process
        with fds:
            for fd in fds:
                try:
                    if not os.readlink(fd.path).startswith("/dev/dri/"):
                        continue
                    with open(f"{proc}/{pid}/fdinfo/{fd.name}") as f:
                        parsed = parse_fdinfo(f.read())
                    if uid is None:
                        with open(f"{proc}/{pid}/cgroup") as f:
                            uid = pod_uid(f.read())
                except (OSError, ValueError, KeyError):
                    continue  # fd or process went away mid-scan, or an unparseable fdinfo
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
        lines += [f'pod_drm_memory_gtt_bytes{{uid="{uid}"}} {b}' for uid, b in sorted(collect().items())]
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
    assert parse_fdinfo(sample) == (("", "24"), 666232 * 1024)
    assert parse_fdinfo("drm-pdev:\t0000:76:00.0\ndrm-client-id:\t1\ndrm-memory-gtt:\t3 MiB\n") == (("0000:76:00.0", "1"), 3 << 20)
    assert parse_fdinfo("pos:\t0\nflags:\t02\n") is None
    cg = "0::/kubepods/burstable/podd9e1bcd4-3c79-41e4-9714-19bc78cdf48a/7e63f4d5\n"
    assert pod_uid(cg) == "d9e1bcd4-3c79-41e4-9714-19bc78cdf48a"
    assert pod_uid("0::/system/runtime\n") is None
    # Real control-2 clients table; a command with a space must still yield its tgid.
    with tempfile.TemporaryDirectory() as dri:
        os.makedirs(f"{dri}/0")
        os.makedirs(f"{dri}/bridges")
        with open(f"{dri}/0/clients", "w") as f:
            f.write("             command  tgid dev master a   uid      magic                                                             name                   id\n"
                    "        llama-server 58578 128   n    n     0          0                                                          <unset>                   19\n"
                    "          gpu worker    11 128   n    n     0          0                                                          <unset>                   21\n")
        assert drm_client_pids(dri) == {"58578", "11"}
    # two fds on client 7 count once, the /dev/null fd is skipped, and pid 12 is not a DRM client
    with tempfile.TemporaryDirectory() as proc, tempfile.TemporaryDirectory() as dri:
        os.makedirs(f"{dri}/0")
        with open(f"{dri}/0/clients", "w") as f:
            f.write("command tgid dev master a uid magic\nllama-server 10 128 n n 0 0\nllama-server 11 128 n n 0 0\n")
        for pid, fd, client in (("10", "3", "7"), ("10", "4", "7"), ("11", "3", "8")):
            os.makedirs(f"{proc}/{pid}/fd", exist_ok=True)
            os.makedirs(f"{proc}/{pid}/fdinfo", exist_ok=True)
            os.symlink("/dev/dri/renderD128", f"{proc}/{pid}/fd/{fd}")
            with open(f"{proc}/{pid}/fdinfo/{fd}", "w") as f:
                f.write(f"drm-client-id:\t{client}\ndrm-memory-gtt:\t1 KiB\n")
            with open(f"{proc}/{pid}/cgroup", "w") as f:
                f.write(cg)
        os.symlink("/dev/null", f"{proc}/10/fd/5")
        # a complete DRM client in another pod, absent from debugfs, so collect must not count it
        os.makedirs(f"{proc}/12/fd")
        os.makedirs(f"{proc}/12/fdinfo")
        os.symlink("/dev/dri/renderD128", f"{proc}/12/fd/3")
        with open(f"{proc}/12/fdinfo/3", "w") as f:
            f.write("drm-client-id:\t9\ndrm-memory-gtt:\t1 KiB\n")
        with open(f"{proc}/12/cgroup", "w") as f:
            f.write("0::/kubepods/besteffort/pod00000000-0000-0000-0000-000000000000/1\n")
        assert collect(proc, dri) == {"d9e1bcd4-3c79-41e4-9714-19bc78cdf48a": 2048}
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        self_check()
    else:
        # single-threaded: overlapping scrapes queue instead of scanning /proc in parallel
        HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
