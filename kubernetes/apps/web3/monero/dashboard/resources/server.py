#!/usr/bin/env python3
"""
P2Pool Data Logger & HTTP Server

This script fetches real-time mining stats from xmrig, pool, and monerod,
logs them in a rolling in-memory buffer (last 24h), serves them via HTTP endpoints,
and fetches XMR prices from multiple sources with fallback.
"""

import http.server
import json
import os
import shutil
import urllib.request
import time
import argparse
import threading
import signal
import sys
from collections import deque

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8080, help="HTTP server port")

parser.add_argument("--data-dir", type=str, default="./p2pool-data", help="Directory to store logs")

parser.add_argument("--wallet", type=str, help="Monero wallet address for p2pool observer")

parser.add_argument("--observer-url", type=str,
                    default="https://nano.p2pool.observer/api",
                    help="p2pool observer API base URL")
args = parser.parse_args()

PORT = args.port
DATA_DIR = args.data_dir
OBSERVER_URL = args.observer_url

WALLET_ADDRESS = args.wallet or ""

LOG_FILE = os.path.join(DATA_DIR, "stats_log.json")   # persistent JSON log file
STATS_MOD_FILE = os.path.join(DATA_DIR, "stats_mod")  # configuration for min payment
MAX_LOG_AGE = 24 * 3600  # seconds, keep last 24h of data

# Service endpoints - use Kubernetes service names
XMRIG_API_URL = os.getenv("XMRIG_API_URL", "http://xmrig.web3.svc.cluster.local:42000/2/summary")
MONEROD_RPC_URL = os.getenv("MONEROD_RPC_URL", "http://monerod.web3.svc.cluster.local:18089/json_rpc")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Using deque for efficient append/pop from left (for rolling window)
log = {
    "timestamps": deque(),
    "myHash": deque(),
    "poolHash": deque(),
    "netHash": deque(),
    "price": deque()
}

# Thread-safe access to log
log_lock = threading.Lock()

# Price changes on minute timescales; the chart has 6 axis ticks over 24h.
# Refetch at most every 5 min instead of every 10s loop iteration — public
# APIs (CoinGecko) rate-limit well below 6 req/min sustained.
PRICE_CACHE_TTL = 300
_price_cache = {"value": 0.0, "ts": 0.0}

def monerod_get_info_request():
    """get_info RPC request, shared by the HTTP proxy and the logger thread."""
    return urllib.request.Request(
        MONEROD_RPC_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": "0", "method": "get_info"}).encode(),
        headers={"Content-Type": "application/json"}
    )

def get_xmr_price():
    """
    Fetch XMR price in EUR from multiple APIs with fallback, cached for
    PRICE_CACHE_TTL seconds. Falls back to the last in-memory value.
    """
    now = time.time()
    if _price_cache["value"] > 0 and now - _price_cache["ts"] < PRICE_CACHE_TTL:
        return _price_cache["value"]
    sources = [
        ("https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=eur",
         lambda d: float(d["monero"]["eur"]), "CoinGecko"),
        ("https://api.kraken.com/0/public/Ticker?pair=XMREUR",
         lambda d: float(d["result"]["XXMRZEUR"]["c"][0]), "Kraken"),
        ("https://api-pub.bitfinex.com/v2/ticker/tXMRUSD", None, "Bitfinex+FX"),
    ]
    for url, parser_func, name in sources:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)
            if name == "Bitfinex+FX":
                # Convert USD -> EUR using Frankfurter API
                usd_to_eur = 1.0
                try:
                    with urllib.request.urlopen("https://api.frankfurter.app/latest?from=USD&to=EUR", timeout=5) as r2:
                        fx_data = json.load(r2)
                        usd_to_eur = float(fx_data["rates"]["EUR"])
                except Exception:
                    pass
                price = float(data[6]) * usd_to_eur
            else:
                price = parser_func(data)
            if price > 0:
                print(f"Price has come from: {name}")
                _price_cache.update(value=price, ts=now)
                return price
        except Exception:
            continue
    # Fallback: last appended in-memory value (no disk round-trip needed)
    with log_lock:
        last_price = float(log["price"][-1]) if log["price"] else 0.0
    print("Price has come from last recorded value")
    return last_price

def get_min_payment_threshold():
    """Read min payment threshold from stats_mod file; fallback to 0.01 XMR"""
    try:
        with open(STATS_MOD_FILE) as f:
            data = json.load(f)
        return data["config"]["minPaymentThreshold"] / 1e12
    except Exception:
        return 0.01

class Handler(http.server.BaseHTTPRequestHandler):
    """
    Handles HTTP GET requests for:
      - /monerod_stats         : proxies Monero daemon get_info
      - /xmrig_summary         : proxies xmrig summary
      - /stats_log.json        : serves rolling log JSON
      - /min_payment_threshold : serves min payout threshold
      - /observer_config       : observer URL + wallet for the frontend
      - /observer/*            : proxies p2pool observer API (CORS)
    Static files are nginx's job (it aliases the p2pool API dir directly);
    anything else is a 404.
    """

    def do_GET(self):
        if self.path == "/monerod_stats":
            self.proxy_monerod()
        elif self.path == "/xmrig_summary":
            self.proxy_xmrig()
        elif self.path == "/stats_log.json":
            self.serve_log()
        elif self.path == "/min_payment_threshold":
            self.send_json({"minPaymentThreshold": get_min_payment_threshold()})
        elif self.path == "/observer_config":
            self.send_json({"wallet": WALLET_ADDRESS, "observer": OBSERVER_URL})
        elif self.path.startswith("/observer/"):
            self.proxy_observer_api()
        else:
            self.send_json_error("Not found", 404)

    def send_json(self, payload, status=200):
        """Serialize payload and send it as a JSON response."""
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, message, status_code):
        """Send a JSON error response."""
        self.send_json({"error": message}, status_code)

    def proxy(self, request, error_prefix, error_status, timeout=5):
        """Stream an upstream JSON response through, relaying Content-Encoding.

        Observer bodies reach 9MB, held whole in a 256Mi container by r.read().
        """
        try:
            r = urllib.request.urlopen(request, timeout=timeout)
        except Exception as e:
            self.send_json_error(f"{error_prefix}: {e}", error_status)
            return
        with r:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if r.headers.get("Content-Encoding"):
                self.send_header("Content-Encoding", r.headers["Content-Encoding"])
            self.end_headers()
            shutil.copyfileobj(r, self.wfile)

    def proxy_xmrig(self):
        """Proxy xmrig summary with graceful fallback when miner is scaled to 0.

        XMRig uses KEDA ScaledObject with minReplicaCount: 0 — when no excess
        solar power, the pod scales down and has no endpoints. Instead of
        crashing with ConnectionRefusedError, return a 503 JSON response.
        """
        self.proxy(urllib.request.Request(XMRIG_API_URL), "XMRig miner offline", 503)

    def proxy_monerod(self):
        """Send a get_info RPC call to monerod and return JSON.

        Returns 503 JSON if monerod is unreachable instead of crashing."""
        self.proxy(monerod_get_info_request(), "Monerod unavailable", 503)

    def serve_log(self):
        """Serve in-memory rolling log as JSON"""
        with log_lock:
            data = {k: list(v) for k, v in log.items()}
        self.send_json(data)

    def proxy_observer_api(self):
        """Proxy requests to p2pool.observer API to avoid CORS issues in browser.

        Routes:
          - /observer/shares?limit=10000          → {base}/shares?limit=10000
          - /observer/payouts/{wallet}            → {base}/payouts/{wallet}
          - /observer/pool_info                   → {base}/pool_info
        """
        # Map /observer/... to the actual API path
        api_path = self.path[len("/observer/"):]  # e.g. "shares?limit=1" or "payouts/..."
        url = f"{OBSERVER_URL}/{api_path}"
        # 9.2MB plain vs 3.0MB gzipped, refetched every 10s per tab; urllib will not ask.
        request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        self.proxy(request, "Observer API error", 502, timeout=15)

def load_log_disk():
    """Load existing stats_log.json into memory at startup"""
    if not os.path.exists(LOG_FILE):
        print("No existing stats_log.json found, starting fresh")
        return
    try:
        with open(LOG_FILE) as f:
            data = json.load(f)
        with log_lock:
            for k in log:
                log[k] = deque(data.get(k, []))
        print(f"Loaded {len(log['timestamps'])} old log entries")
    except Exception as e:
        print(f"Error reading existing stats_log.json, starting fresh: {e}")

def append_log(myHash, poolHash, netHash, price):
    """
    Append a new data point to the in-memory rolling log
    Removes entries older than MAX_LOG_AGE (24h)
    """
    ts = int(time.time())
    cutoff = ts - MAX_LOG_AGE
    with log_lock:
        log["timestamps"].append(ts)
        log["myHash"].append(myHash)
        log["poolHash"].append(poolHash)
        log["netHash"].append(netHash)
        log["price"].append(price)

        # Remove old entries
        while log["timestamps"] and log["timestamps"][0] < cutoff:
            for k in log:
                log[k].popleft()

def save_log_disk():
    """Write rolling log to disk atomically"""
    tmp_file = LOG_FILE + ".tmp"
    with log_lock:
        data = {k: list(v) for k, v in log.items()}
    with open(tmp_file, "w") as f:
        json.dump(data, f)
    os.replace(tmp_file, LOG_FILE)

def log_loop():
    """
    Continuously fetch stats from xmrig, pool, monerod, and XMR price.
    Appends to the in-memory rolling log every 10 seconds; persists to disk
    every 5 minutes (the file only exists for chart continuity across pod
    restarts — the PVC is 3x-replicated Ceph, don't rewrite 0.5MB every 10s).
    Runs in a separate daemon thread.
    """
    last_save = 0
    while not shutdown_event.is_set():
        try:
            # Fetch instantaneous hashrates
            xmrig = json.loads(
                urllib.request.urlopen(XMRIG_API_URL, timeout=5).read()
            )
            myHash = xmrig["hashrate"]["total"][0]

            # Read pool stats directly from file
            pool_stats_path = os.path.join(DATA_DIR, "pool", "stats")
            with open(pool_stats_path, "r") as f:
                pool = json.load(f)
            poolHash = pool["pool_statistics"]["hashRate"]

            # Fetch network difficulty from monerod
            net = json.loads(urllib.request.urlopen(monerod_get_info_request(), timeout=5).read())
            netHash = net["result"]["difficulty"] / 120

            # Fetch XMR price
            price = get_xmr_price()

            # Append to in-memory log
            append_log(myHash, poolHash, netHash, price)

            # Periodically save to disk (every 5 min)
            if time.time() - last_save > 300:
                save_log_disk()
                last_save = time.time()

        except Exception as e:
            if not shutdown_event.is_set():
                print("Log error:", e)

        shutdown_event.wait(10)  # sleep or wait until shutdown

shutdown_event = threading.Event()

# Load old logs before appending new info to prevent the log being overwritten
load_log_disk()
threading.Thread(target=log_loop, daemon=True).start()

# Exec-form container command makes python PID 1: translate SIGTERM into the
# same clean-shutdown path as Ctrl+C so the final save_log_disk() runs.
def _sigterm(*_):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, _sigterm)

class Server(http.server.ThreadingHTTPServer):
    # ThreadingHTTPServer daemonises request threads; ThreadingTCPServer did not.
    # Non-daemon threads make server_close() wait for an in-flight observer proxy
    # instead of truncating it mid-write on SIGTERM.
    daemon_threads = False

print(f"Serving HTTP on 0.0.0.0:{PORT}")

try:
    with Server(("", PORT), Handler) as httpd:
        httpd.serve_forever()
except KeyboardInterrupt:
    print("\nCTRL+C received, shutting down cleanly...")
finally:
    shutdown_event.set()   # signal logger thread to stop
    save_log_disk()        # persist current log to disk
    print("Server stopped cleanly.")
    sys.exit(0)
