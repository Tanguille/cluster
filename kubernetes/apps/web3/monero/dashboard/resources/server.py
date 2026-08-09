#!/usr/bin/env python3
"""
P2Pool Data Logger & HTTP Server

This script fetches real-time mining stats from xmrig, pool, and monerod, logs
them in two rolling in-memory tiers (10s samples for 24h, 5-minute means for
90d), serves them via HTTP endpoints, and fetches XMR prices from multiple
sources with fallback.
"""

import bisect
import http.server
import json
import math
import os
import shutil
import urllib.request
import time
import argparse
import threading
import signal
import statistics
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from urllib.parse import parse_qs

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8080, help="HTTP server port")

parser.add_argument("--data-dir", type=str, default="./p2pool-data", help="Directory to store logs")

parser.add_argument("--wallet", type=str, help="Monero wallet address for p2pool observer")

parser.add_argument("--observer-url", type=str,
                    default="https://nano.p2pool.observer/api",
                    help="p2pool observer API base URL")
# parse_known_args, not parse_args: every option has a default, so the test
# module can import this file without argparse choking on the runner's argv.
args = parser.parse_known_args()[0]

PORT = args.port
DATA_DIR = args.data_dir
OBSERVER_URL = args.observer_url

WALLET_ADDRESS = args.wallet or ""

LOG_FILE = os.path.join(DATA_DIR, "stats_log.json")      # 10s samples, last 24h
ROLLUP_FILE = os.path.join(DATA_DIR, "stats_rollup.json")  # 5min means, last 90d
STATS_MOD_FILE = os.path.join(DATA_DIR, "stats_mod")  # configuration for min payment
MAX_LOG_AGE = 24 * 3600  # seconds, keep last 24h of data

# Second retention tier. 10s samples cost 8640 points/day, so the 24h buffer is
# as far back as the fine log can reach without the JSON outgrowing the browser.
# 5-minute means over 90d are 25920 points — the same order of magnitude, three
# months of range.
ROLLUP_INTERVAL = 300
ROLLUP_MAX_AGE = 90 * 86400

# Charts are ~600 CSS px wide, so more points than this land sub-pixel. Bounds
# the response at every range: 90d downsamples to the same payload as 1h.
CHART_MAX_POINTS = 720

SERIES = ("myHash", "poolHash", "netHash", "price")

# Service endpoints - use Kubernetes service names
XMRIG_API_URL = os.getenv("XMRIG_API_URL", "http://xmrig.web3.svc.cluster.local:42000/2/summary")
MONEROD_RPC_URL = os.getenv("MONEROD_RPC_URL", "http://monerod.web3.svc.cluster.local:18089/json_rpc")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

def num(v):
    """Coerce a series value to a finite float.

    xmrig reports hashrate.total[0] as null while the miner is idle, and that
    null reaches both tiers: it breaks accumulate_rollup's += and downsample's
    sum(). Idle means 0 H/s, so clamp it here rather than at every consumer.
    """
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else 0.0

def new_series():
    """Empty column store. deque gives O(1) eviction from the left."""
    return {"timestamps": deque(), **{k: deque() for k in SERIES}}

log = new_series()
rollup = new_series()

# Open rollup bucket: sums plus a count, divided into a mean when the bucket closes.
_bucket = {"key": None, "count": 0, **{k: 0.0 for k in SERIES}}

# Thread-safe access to log and rollup
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
      - /stats_history.json    : serves a downsampled window for the charts
      - /min_payment_threshold : serves min payout threshold
      - /observer_config       : observer URL + wallet for the frontend
      - /observer/*            : proxies p2pool observer API (CORS)
    Static files are nginx's job (it aliases the p2pool API dir directly);
    anything else is a 404.
    """

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/monerod_stats":
            self.proxy_monerod()
        elif path == "/xmrig_summary":
            self.proxy_xmrig()
        elif path == "/stats_log.json":
            self.serve_log()
        elif path == "/stats_history.json":
            self.serve_history(query)
        elif path == "/min_payment_threshold":
            self.send_json({"minPaymentThreshold": get_min_payment_threshold()})
        elif path == "/observer_config":
            self.send_json({"wallet": WALLET_ADDRESS, "observer": OBSERVER_URL})
        elif path.startswith("/observer/"):
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

    def serve_history(self, query):
        """Serve a bounded, downsampled window for the charts: ?hours=<float>."""
        try:
            hours = float(parse_qs(query).get("hours", ["24"])[0])
        except ValueError:
            hours = 24.0
        if not math.isfinite(hours):
            hours = 24.0
        hours = min(max(hours, 0.1), ROLLUP_MAX_AGE / 3600)
        # The 10s log only reaches back MAX_LOG_AGE; beyond that the rollup is
        # the only source, and inside it the fine samples give a truer shape.
        source = log if hours * 3600 <= MAX_LOG_AGE else rollup
        self.send_json(break_gaps(downsample(source, hours)))

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

def load_tier(path, target, keep_missing=False):
    """Restore one retention tier from disk; leave it empty if unreadable.

    keep_missing distinguishes the two tiers' nulls. In the 10s log a null is
    junk from a legacy file and coerces to 0.0; in the rollup it is the
    backfill's "never measured" marker and has to survive the round trip, or
    the first restart turns every price-only bucket into a real 0 H/s reading.
    """
    if not os.path.exists(path):
        print(f"No existing {os.path.basename(path)} found, starting fresh")
        return
    try:
        with open(path) as f:
            data = json.load(f)
        target["timestamps"] = deque(data.get("timestamps", []))
        for k in SERIES:
            target[k] = deque(None if keep_missing and v is None else num(v)
                              for v in data.get(k, []))
        print(f"Loaded {len(target['timestamps'])} entries from {os.path.basename(path)}")
    except Exception as e:
        print(f"Error reading {os.path.basename(path)}, starting fresh: {e}")

def load_log_disk():
    """Load both retention tiers at startup, back-filling the rollup if it is new."""
    with log_lock:
        load_tier(LOG_FILE, log)
        load_tier(ROLLUP_FILE, rollup, keep_missing=True)
        # First start after the rollup was introduced: replay the 24h log through
        # the bucketer so the long ranges are not blank for the first three months.
        if not rollup["timestamps"] and log["timestamps"]:
            columns = {k: list(log[k]) for k in SERIES}
            # A ragged column (missing/short series in stats_log.json) must not
            # index past its own length and crash startup before the HTTP server binds.
            usable = min(len(log["timestamps"]), *(len(c) for c in columns.values()))
            for i, ts in enumerate(list(log["timestamps"])[:usable]):
                accumulate_rollup(ts, {k: columns[k][i] for k in SERIES})
            print(f"Seeded {len(rollup['timestamps'])} rollup buckets from the 24h log")

def accumulate_rollup(ts, values):
    """Fold one 10s sample into its 5-minute bucket, emitting the mean once it closes.

    Caller holds log_lock. A bucket is only written out when a sample from the
    *next* one arrives, so the in-progress bucket never reaches the chart
    half-averaged.
    """
    key = ts // ROLLUP_INTERVAL
    if _bucket["key"] is not None and key != _bucket["key"]:
        rollup["timestamps"].append(_bucket["key"] * ROLLUP_INTERVAL)
        for k in SERIES:
            rollup[k].append(_bucket[k] / _bucket["count"])
        cutoff = ts - ROLLUP_MAX_AGE
        while rollup["timestamps"] and rollup["timestamps"][0] < cutoff:
            for series in rollup.values():
                series.popleft()
        _bucket.update(count=0, **{k: 0.0 for k in SERIES})
    _bucket["key"] = key
    _bucket["count"] += 1
    for k in SERIES:
        _bucket[k] += values[k]

def append_log(myHash, poolHash, netHash, price):
    """
    Append a new data point to the in-memory rolling log
    Removes entries older than MAX_LOG_AGE (24h)
    """
    ts = int(time.time())
    cutoff = ts - MAX_LOG_AGE
    values = dict(zip(SERIES, map(num, (myHash, poolHash, netHash, price))))
    with log_lock:
        log["timestamps"].append(ts)
        for k in SERIES:
            log[k].append(values[k])

        # Remove old entries
        while log["timestamps"] and log["timestamps"][0] < cutoff:
            for series in log.values():
                series.popleft()

        accumulate_rollup(ts, values)

def downsample(source, hours, max_points=CHART_MAX_POINTS):
    """Mean-aggregate the last `hours` of `source` into at most max_points buckets.

    Mean rather than decimation: a miner that is gated off for part of a bucket
    should pull that bucket's hashrate down, not vanish between samples.
    """
    cutoff = time.time() - hours * 3600
    with log_lock:
        # bisect only needs __len__/__getitem__, so the deque is searched in
        # place: a 1h request against a full 90d rollup then copies ~12 of its
        # 25920 buckets instead of all of them, and holds the lock for less.
        start = bisect.bisect_left(source["timestamps"], cutoff)
        timestamps = list(islice(source["timestamps"], start, None))
        series = {k: list(islice(source[k], start, None)) for k in SERIES}

    if not timestamps:
        return new_empty_window()

    width = math.ceil(len(timestamps) / max_points)
    out = new_empty_window()
    for i in range(0, len(timestamps), width):
        out["timestamps"].append(timestamps[i])
        for k in SERIES:
            # None marks "never measured" — a backfilled price bucket has no
            # hashrate. Averaging it as 0 would invent a reading.
            chunk = [v for v in series[k][i:i + width] if v is not None]
            out[k].append(sum(chunk) / len(chunk) if chunk else None)
    return out

def break_gaps(window, tolerance=3):
    """Insert a null datum wherever sampling stopped, so the chart breaks there.

    Chart.js draws a straight segment through missing points, which renders a
    logger outage as steady mining at the pre-outage rate. The threshold is the
    window's own median step rather than a tier constant, so it self-calibrates
    across both retention tiers and every range.
    """
    stamps = window["timestamps"]
    if len(stamps) < 3:
        return window
    limit = tolerance * statistics.median(b - a for a, b in zip(stamps, stamps[1:]))

    out = new_empty_window()
    for i, ts in enumerate(stamps):
        if i and ts - stamps[i - 1] > limit:
            out["timestamps"].append(stamps[i - 1] + (ts - stamps[i - 1]) // 2)
            for k in SERIES:
                out[k].append(None)
        out["timestamps"].append(ts)
        for k in SERIES:
            out[k].append(window[k][i])
    return out

def new_empty_window():
    """Empty result shape, shared by the no-data path and the accumulator."""
    return {"timestamps": [], **{k: [] for k in SERIES}}

def save_log_disk():
    """Write both retention tiers to disk atomically"""
    with log_lock:
        tiers = [
            (LOG_FILE, {k: list(v) for k, v in log.items()}),
            (ROLLUP_FILE, {k: list(v) for k, v in rollup.items()})
        ]
    for path, data in tiers:
        tmp_file = path + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(data, f)
        os.replace(tmp_file, path)

def _fetch_json(request, timeout=5):
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)

PRICE_HISTORY_URL = (f"https://api.coingecko.com/api/v3/coins/monero/market_chart"
                     f"?vs_currency=eur&days={ROLLUP_MAX_AGE // 86400}")

def backfill_price_history():
    """Seed the rollup's price series for buckets the logger never recorded.

    Until the logger learned to run through an idle miner it only sampled while
    xmrig was up, so on the long ranges the price line exists exactly where the
    solar gate happened to be open. CoinGecko serves hourly closes over the
    retention horizon; fill only the buckets we have nothing for, leaving the
    hashrate series None there rather than claiming we measured 0 H/s.
    """
    try:
        points = _fetch_json(PRICE_HISTORY_URL, timeout=15)["prices"]
    except Exception as e:
        print(f"Price backfill unavailable, charting logged prices only: {e}")
        return

    with log_lock:
        buckets = {ts: {k: rollup[k][i] for k in SERIES}
                   for i, ts in enumerate(rollup["timestamps"])}
        logged = len(buckets)
        for ms, price in points:
            ts = int(ms / 1000) // ROLLUP_INTERVAL * ROLLUP_INTERVAL
            # setdefault, not assignment: a bucket we logged has a real price
            # and a real hashrate, and CoinGecko must not flatten either.
            buckets.setdefault(ts, {**{k: None for k in SERIES}, "price": price})
        if len(buckets) == logged:
            return

        rollup["timestamps"] = deque(sorted(buckets))
        for k in SERIES:
            rollup[k] = deque(buckets[ts][k] for ts in rollup["timestamps"])
    print(f"Backfilled {len(buckets) - logged} price-only rollup buckets from CoinGecko")

def read_pool_hashrate():
    """p2pool writes its stats to the shared API dir; no HTTP hop needed."""
    with open(os.path.join(DATA_DIR, "pool", "stats")) as f:
        return json.load(f)["pool_statistics"]["hashRate"]

def last_logged(key):
    """Most recent value for `key`, or 0.0 on an empty log."""
    with log_lock:
        return log[key][-1] if log[key] else 0.0

def fetch_or(source, fallback):
    """Read one series' source, substituting `fallback` if it is unavailable.

    Every source used to share one try block, so any single failure discarded
    the whole sample. That is what punched 17.7h of holes into a 24h log: xmrig
    is unreachable by design most nights, and a monerod blip or a pool stats
    file caught mid-rename does the same for a tick.
    """
    try:
        return source()
    except Exception:
        return fallback

def log_loop():
    """
    Continuously fetch stats from xmrig, pool, monerod, and XMR price.
    Appends to the in-memory rolling log every 10 seconds; persists to disk
    every 5 minutes (the file only exists for chart continuity across pod
    restarts — the PVC is 3x-replicated Ceph, don't rewrite 0.5MB every 10s).
    Runs in a separate daemon thread.
    """
    last_save = 0
    # Runs here rather than in main(): a slow CoinGecko must not delay the bind.
    backfill_price_history()
    with ThreadPoolExecutor(max_workers=2) as pool_executor:
        while not shutdown_event.is_set():
            try:
                # xmrig and monerod are independent HTTP calls (5s timeout each) —
                # fire both concurrently instead of stacking their worst-case latency
                xmrig_future = pool_executor.submit(_fetch_json, XMRIG_API_URL)
                net_future = pool_executor.submit(_fetch_json, monerod_get_info_request())

                # KEDA scales xmrig to 0 replicas without excess solar, so a
                # gated-off miner really is 0 H/s. The network and the pool did
                # not stop when their read blipped, so those carry forward.
                myHash = fetch_or(lambda: xmrig_future.result()["hashrate"]["total"][0], 0.0)
                poolHash = fetch_or(read_pool_hashrate, last_logged("poolHash"))
                netHash = fetch_or(lambda: net_future.result()["result"]["difficulty"] / 120,
                                   last_logged("netHash"))

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

class Server(http.server.ThreadingHTTPServer):
    # ThreadingHTTPServer daemonises request threads; ThreadingTCPServer did not.
    # Non-daemon threads make server_close() wait for an in-flight observer proxy
    # instead of truncating it mid-write on SIGTERM.
    daemon_threads = False

# Exec-form container command makes python PID 1: translate SIGTERM into the
# same clean-shutdown path as Ctrl+C so the final save_log_disk() runs.
def _sigterm(*_):
    raise KeyboardInterrupt

def main():
    # Load old logs before appending new info to prevent the log being overwritten
    load_log_disk()
    threading.Thread(target=log_loop, daemon=True).start()
    signal.signal(signal.SIGTERM, _sigterm)

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

if __name__ == "__main__":
    main()
