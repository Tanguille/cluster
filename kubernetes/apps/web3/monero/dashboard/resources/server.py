#!/usr/bin/env python3
"""
P2Pool Data Logger & HTTP Server

This script fetches real-time mining stats from xmrig, pool, and monerod,
logs them in a rolling in-memory buffer (last 24h), serves them via HTTP endpoints,
and fetches XMR prices from multiple sources with fallback.
"""

import http.server
import socketserver
import json
import os
import urllib.request
import time
import argparse
import threading
import sys
from collections import deque

# ==============================
# COMMAND LINE ARGUMENTS
# ==============================
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8080, help="HTTP server port")

parser.add_argument("--data-dir", type=str, default="./p2pool-data", help="Directory to store logs")

parser.add_argument("--wallet", type=str, help="Monero wallet address for p2pool observer")

parser.add_argument("--normal-p2pool", action="store_true", help="Enable p2pool.observer")
parser.add_argument("--mini-p2pool", action="store_true", help="Enable mini.p2pool.observer")
parser.add_argument("--nano-p2pool", action="store_true", help="Enable nano.p2pool.observer")
args = parser.parse_args()

PORT = args.port
DATA_DIR = args.data_dir

# ==============================
# P2POOL OBSERVER CONFIG
# ==============================

OBSERVER_DOMAINS = []

if args.normal_p2pool:
    OBSERVER_DOMAINS.append("https://p2pool.observer/api")
if args.mini_p2pool:
    OBSERVER_DOMAINS.append("https://mini.p2pool.observer/api")
if args.nano_p2pool:
    OBSERVER_DOMAINS.append("https://nano.p2pool.observer/api")

WALLET_ADDRESS = args.wallet or ""

LOG_FILE = os.path.join(DATA_DIR, "stats_log.json")   # persistent JSON log file
STATS_MOD_FILE = os.path.join(DATA_DIR, "stats_mod")  # configuration for min payment
MAX_LOG_AGE = 24 * 3600  # seconds, keep last 24h of data

# Service endpoints - use Kubernetes service names
XMRIG_API_URL = os.getenv("XMRIG_API_URL", "http://xmrig.web3.svc.cluster.local:42000/2/summary")
MONEROD_RPC_URL = os.getenv("MONEROD_RPC_URL", "http://monerod.web3.svc.cluster.local:18089/json_rpc")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# ==============================
# IN-MEMORY ROLLING LOGS
# ==============================
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

# ==============================
# HELPER FUNCTIONS
# ==============================

def get_last_price():
    """Return the last recorded XMR price from disk log"""
    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
            if data["price"]:
                return float(data["price"][-1])
    except Exception:
        pass
    return 0.0

def get_xmr_price():
    """
    Fetch XMR price in EUR from multiple APIs with fallback.
    Sources: CoinGecko, Kraken, Bitfinex+FX conversion, price2sheet
    """
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
                return price
        except Exception:
            continue
    # Fallback to last recorded price
    last_price = get_last_price()
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

# ==============================
# HTTP SERVER HANDLER
# ==============================

class Handler(http.server.SimpleHTTPRequestHandler):
    """
    Handles HTTP GET requests for:
      - /monerod_stats      : proxies Monero daemon get_info
      - /xmrig_summary       : proxies xmrig summary
      - /stats_log.json      : serves rolling log JSON
      - /min_payment_threshold : serves min payout threshold
      - all other paths      : served as static files from DATA_DIR
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DATA_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/monerod_stats":
            self.proxy_monerod()
        elif self.path == "/xmrig_summary":
            self.proxy(XMRIG_API_URL)
        elif self.path == "/stats_log.json":
            self.serve_log()
        elif self.path == "/min_payment_threshold":
            self.serve_threshold()
        elif self.path == "/observer_config":
            self.serve_observer_config()
        else:
            super().do_GET()

    def proxy(self, url):
        """Fetch JSON from a local service and return to client"""
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def proxy_monerod(self):
        """Send a get_info RPC call to monerod and return JSON"""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": "0",
            "method": "get_info"
        }).encode()
        req = urllib.request.Request(
            MONEROD_RPC_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(r.read())

    def serve_log(self):
        """Serve in-memory rolling log as JSON"""
        with log_lock:
            data = {k: list(v) for k, v in log.items()}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def serve_threshold(self):
        """Serve min payment threshold as JSON"""
        threshold = get_min_payment_threshold()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"minPaymentThreshold": threshold}).encode())

    def serve_observer_config(self):
        data = {
            "wallet": WALLET_ADDRESS,
            "observers": OBSERVER_DOMAINS
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

# ==============================
# LOGGING FUNCTIONS
# ==============================

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

# ==============================
# LOGGER LOOP THREAD
# ==============================

def log_loop():
    """
    Continuously fetch stats from xmrig, pool, monerod, and XMR price.
    Appends to rolling log and saves to disk every 10 seconds.
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
            req = urllib.request.Request(
                MONEROD_RPC_URL,
                data=json.dumps({"jsonrpc": "2.0", "id": "0", "method": "get_info"}).encode(),
                headers={"Content-Type": "application/json"}
            )
            net = json.loads(urllib.request.urlopen(req, timeout=5).read())
            netHash = net["result"]["difficulty"] / 120

            # Fetch XMR price
            price = get_xmr_price()

            # Append to in-memory log
            append_log(myHash, poolHash, netHash, price)

            # Periodically save to disk (every 10s)
            if time.time() - last_save > 10:
                save_log_disk()
                last_save = time.time()

        except Exception as e:
            if not shutdown_event.is_set():
                print("Log error:", e)

        shutdown_event.wait(10)  # sleep or wait until shutdown

# ==============================
# SHUTDOWN EVENT
# ==============================
shutdown_event = threading.Event()

# ==============================
# START LOGGER THREAD
# ==============================
# Load old logs before appending new info to prevent the log being overwritten
load_log_disk()
threading.Thread(target=log_loop, daemon=True).start()

# ==============================
# START HTTP SERVER
# ==============================
socketserver.ThreadingTCPServer.allow_reuse_address = True
print(f"Serving HTTP on 0.0.0.0:{PORT}")

try:
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
except KeyboardInterrupt:
    print("\nCTRL+C received, shutting down cleanly...")
finally:
    shutdown_event.set()   # signal logger thread to stop
    save_log_disk()        # persist current log to disk
    print("Server stopped cleanly.")
    sys.exit(0)
