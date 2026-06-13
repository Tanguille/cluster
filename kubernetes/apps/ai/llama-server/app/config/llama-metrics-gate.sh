#!/bin/sh
# Hot-only metrics gate (non-Python replacement for PR #3042). Scrapes the router with
# autoload=false so a scrape never force-loads a cold model, then serves a Prometheus file
# (hot/cold state + passthrough llamacpp:* when hot) via busybox httpd — always HTTP 200, so the
# vmagent target never flaps while qwen-3.6 is cold. Single-model by design: iterate the router's
# /v1/models if a second model starts serving traffic.
# set -u (not -e): keep the loop alive through transient upstream failures.
set -u

URL="http://127.0.0.1:8080/metrics?model=qwen-3.6&autoload=false"
OUT=/run/metrics/metrics
LASTOK=0

: >"$OUT" # seed so the first scrape is 200 before the loop's first write

(
  while true; do
    if BODY="$(wget -qO- "$URL" 2>/dev/null)" && [ -n "$BODY" ]; then
      LOADED=1; UP=1; CODE=200; LASTOK="$(date +%s)"
    else
      LOADED=0; UP=0; CODE=400; BODY=""
    fi
    {
      echo "# HELP llama_model_loaded 1 when live llama.cpp metrics were collected without autoloading."
      echo "# TYPE llama_model_loaded gauge"
      echo "llama_model_loaded{model=\"qwen-3.6\"} $LOADED"
      echo "# HELP llama_upstream_metrics_up 1 when the upstream metrics request succeeded."
      echo "# TYPE llama_upstream_metrics_up gauge"
      echo "llama_upstream_metrics_up{model=\"qwen-3.6\"} $UP"
      echo "# HELP llama_exporter_last_successful_scrape_timestamp_seconds Unix time of the last successful upstream scrape."
      echo "# TYPE llama_exporter_last_successful_scrape_timestamp_seconds gauge"
      echo "llama_exporter_last_successful_scrape_timestamp_seconds $LASTOK"
      echo "# HELP llama_exporter_upstream_http_status Last upstream metrics HTTP status (200 = hot, 400 = cold/unloaded)."
      echo "# TYPE llama_exporter_upstream_http_status gauge"
      echo "llama_exporter_upstream_http_status{model=\"qwen-3.6\"} $CODE"
      if [ -n "$BODY" ]; then printf '%s\n' "$BODY"; fi
    } >"$OUT.tmp" && mv "$OUT.tmp" "$OUT"
    sleep 55 # just under the 1m scrape — one fresh sample per scrape, not ~4x
  done
) &

exec httpd -f -v -p 9108 -h /run/metrics
