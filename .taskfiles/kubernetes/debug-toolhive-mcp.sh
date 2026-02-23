#!/usr/bin/env bash
# Collect status and logs for ToolHive MCP servers (HA, Grafana, SearXNG) for debugging.
# Run: task kubernetes:debug-toolhive-mcp

set -euo pipefail

NS="${TOOLHIVE_NAMESPACE:-ai}"
# ToolHive creates deployments named mcp-<mcpserver-name>-proxy; list pods by name pattern
echo "=== Pods (mcp-*-proxy) ==="
kubectl get pods -n "$NS" -o wide 2>/dev/null | grep -E 'NAME|mcp-|homeassistant|grafana|searxng' || kubectl get pods -n "$NS" -o wide

echo ""
echo "=== Deployments/StatefulSets (mcp proxies) ==="
kubectl get deploy,sts -n "$NS" 2>/dev/null | grep -E 'NAME|mcp-|homeassistant|grafana|searxng' || kubectl get deploy,sts -n "$NS"

echo ""
echo "=== Describe non-Running pods (any in namespace) ==="
for pod in $(kubectl get pods -n "$NS" --no-headers 2>/dev/null | awk '$3 != "Running" && $3 != "Completed" {print $1}'); do
  echo "--- $pod ---"
  kubectl describe pod -n "$NS" "$pod" | tail -40
  echo ""
done

# ToolHive operator names deployments/statefulsets after MCPServer name (grafana, homeassistant, searxng)
echo "=== Home Assistant proxy logs (mcp container, last 80 lines) ==="
kubectl logs -n "$NS" deployment/homeassistant -c mcp --tail=80 2>/dev/null || \
kubectl logs -n "$NS" statefulset/homeassistant -c mcp --tail=80 2>/dev/null || \
{ pod_ha=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep -E '^homeassistant-[0-9a-z]+-' | awk '{print $1; exit}'); \
  [[ -n "$pod_ha" ]] && kubectl logs -n "$NS" "$pod_ha" -c mcp --tail=80 2>/dev/null; } || echo "(no homeassistant proxy logs found)"

echo ""
echo "=== Grafana MCP server logs (StatefulSet grafana-0, actual mcp/grafana process) ==="
# ToolHive runs the MCP server in the StatefulSet pod (grafana-0); gateway talks to Deployment proxy which attaches to it
kubectl logs -n "$NS" grafana-0 -c mcp --tail=80 2>/dev/null || \
kubectl logs -n "$NS" grafana-0 --all-containers --tail=80 2>/dev/null || echo "(no logs from grafana-0)"

echo ""
echo "=== Grafana proxy/runner logs (Deployment pod, attaches to grafana-0) ==="
pod_grafana=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep -E '^grafana-[0-9a-z]+-[a-z0-9]+ ' | awk '{print $1; exit}')
if [[ -n "$pod_grafana" ]]; then
  kubectl logs -n "$NS" "$pod_grafana" -c mcp --tail=40 2>/dev/null || \
  kubectl logs -n "$NS" "$pod_grafana" --tail=40 2>/dev/null || echo "(no logs from $pod_grafana)"
else
  kubectl logs -n "$NS" deployment/grafana --tail=40 2>/dev/null || echo "(no grafana deployment logs found)"
fi

echo ""
echo "=== SearXNG proxy logs (last 80 lines) ==="
pod_sx=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep -E '^searxng-[0-9a-z]+-[a-z0-9]+ ' | awk '{print $1; exit}')
if [[ -n "$pod_sx" ]]; then
  kubectl logs -n "$NS" "$pod_sx" -c mcp --tail=80 2>/dev/null || \
  kubectl logs -n "$NS" "$pod_sx" --tail=80 2>/dev/null || echo "(no logs from $pod_sx)"
else
  kubectl logs -n "$NS" deployment/searxng --tail=80 2>/dev/null || \
  kubectl logs -n "$NS" deployment/searxng -c mcp --tail=80 2>/dev/null || echo "(no searxng proxy logs found)"
fi

echo ""
echo "=== SearXNG service in default namespace (for SEARXNG_URL) ==="
kubectl get svc -n default -l app.kubernetes.io/name=searxng -o wide 2>/dev/null || kubectl get svc -n default 2>/dev/null | grep -E 'NAME|searxng'

echo ""
echo "=== vmcp-tools-gateway Service backends (Endpoints vs EndpointSlices) ==="
# Legacy Endpoints: one object per Service, same name as Service (deprecated in K8s 1.33+)
kubectl get endpoints -n "$NS" vmcp-tools-gateway 2>/dev/null || true
# EndpointSlices: use label (controller gives slices names like vmcp-tools-gateway-<suffix>, not the Service name)
echo "EndpointSlices for vmcp-tools-gateway (label selector):"
kubectl get endpointslice -n "$NS" -l kubernetes.io/service-name=vmcp-tools-gateway 2>/dev/null || true

echo ""
echo "=== VirtualMCPServer tools-gateway status (discoveredBackends) ==="
kubectl get virtualmcpserver tools-gateway -n "$NS" -o jsonpath='{.status.discoveredBackends}' 2>/dev/null | jq . 2>/dev/null || kubectl get virtualmcpserver tools-gateway -n "$NS" -o yaml 2>/dev/null | grep -A 200 'discoveredBackends'

echo ""
echo "=== tools-gateway logs (last 30 lines) ==="
kubectl logs -n "$NS" deployment/tools-gateway --tail=30 2>/dev/null || echo "(tools-gateway not found)"
