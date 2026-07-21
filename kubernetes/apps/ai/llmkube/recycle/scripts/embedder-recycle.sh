#!/bin/sh
set -eu

api="https://kubernetes.default.svc/apis/apps/v1/namespaces/ai/deployments"
ca=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
token="$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)"
last_timestamp=

json_field() {
  printf '%s' "$1" | sed -n "s/.*\"$2\":\([^,}]*\).*/\1/p" | sed -n '1p'
}

at_least_one() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) [ "$1" -ge 1 ] ;;
  esac
}

wait_ready() {
  deployment="$1"
  generation="$2"
  attempt=0
  while [ "$attempt" -lt 12 ]; do
    status="$(curl -fsS --connect-timeout 10 --max-time 30 --cacert "$ca" -H "Authorization: Bearer $token" "$api/$deployment")"
    observed="$(json_field "$status" observedGeneration || true)"
    updated="$(json_field "$status" updatedReplicas || true)"
    available="$(json_field "$status" availableReplicas || true)"
    ready="$(json_field "$status" readyReplicas || true)"
    if [ "$observed" = "$generation" ] && at_least_one "$updated" && at_least_one "$available" && at_least_one "$ready"; then
      return 0
    fi
    sleep 5
    attempt=$((attempt + 1))
  done
  return 1
}

for deployment in qwen3-embedding vmcp-embedding; do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$timestamp" = "$last_timestamp" ]; then
    sleep 1
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  last_timestamp="$timestamp"
  patched="$(curl -fsS --connect-timeout 10 --max-time 30 --cacert "$ca" -H "Authorization: Bearer $token" -H "Content-Type: application/merge-patch+json" -X PATCH --data "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kubectl.kubernetes.io/restartedAt\":\"$timestamp\"}}}}}" "$api/$deployment")"
  generation="$(json_field "$patched" generation || true)"
  case "$generation" in
    ''|*[!0-9]*) exit 1 ;;
  esac
  wait_ready "$deployment" "$generation"
done
