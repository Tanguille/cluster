# Useful Commands for Kubernetes / Talos

Short reference for debugging and day-to-day ops. Prefer **GitOps**: change manifests in Git and run `task reconcile` rather than editing resources in-cluster.

---

## Flux / GitOps

```bash
# List tasks (primary entry point)
task

# Pull latest from Git and reconcile (preferred after config changes)
task reconcile

# Reconcile a single Kustomization with source
flux reconcile kustomization <name> --with-source

# Reconcile GitRepository (refresh from remote)
flux reconcile source git flux-system

# Status overview
flux get kustomizations
flux get helmreleases -A
```

---

## Talos

```bash
# Generate Talos config (from talconfig)
task talos:generate-config

# Apply config to a node / upgrade node / upgrade Kubernetes
task talos:apply-node IP=<node-ip>
task talos:upgrade-node IP=<node-ip>
task talos:upgrade-k8s
```

**Update schematics (build both from `talos/schematic.yaml` and write installer URLs into `talconfig.yaml`):**

```bash
task talos:schematics-update
```

Then run `task talos:generate-config` and apply or upgrade nodes as needed.

---

## Pods & workloads

```bash
# Restart deployment (e.g. after ConfigMap change)
kubectl rollout restart deployment/<name> -n <ns>
kubectl rollout status deployment/<name> -n <ns>

# Scale
kubectl scale deployment/<name> -n <ns> --replicas=<n>

# Debug pod with networking tools (exit with Ctrl+D or 'exit')
kubectl run tmp-shell --rm -i --tty --image nicolaka/netshoot -n <ns> -- /bin/bash

# Logs
kubectl logs -n <ns> deployment/<name> -f
kubectl logs -n <ns> <pod-name> -c <container> --tail=100

# Inspect
kubectl describe pod -n <ns> <pod-name>
kubectl get pod -n <ns> <pod-name> -o yaml
```

---

## Networking

```bash
# Services and backends (prefer EndpointSlices; slice names are <svc>-<suffix>)
kubectl get svc -n <ns>
kubectl get endpointslice -n <ns> -l kubernetes.io/service-name=<service-name>

# HTTPRoutes (Gateway API)
kubectl get httproute -A

# NetworkPolicies
kubectl get networkpolicies -n <ns>

# From inside a pod: test service DNS
curl http://<service>.<ns>.svc.cluster.local
```

---

## Storage & exec

```bash
# PVCs
kubectl get pvc -A

# Mount usage inside a pod
kubectl exec -n <ns> deployment/<name> -- df -h /path

# Run a command in a pod (replace deployment/<name> with pod name if needed)
kubectl exec -it -n <ns> deployment/<name> -- /bin/sh
```

Optional: [kubectl-browse-pvc](https://github.com/clbx/kubectl-browse-pvc) to browse PVCs.

---

## Troubleshooting failed HelmReleases

Prefer fixing the cause in Git (values, chart version, dependencies) and running `task reconcile`. If a release is stuck and you need to force recreation:

```bash
# 1. Inspect status and events
kubectl describe helmrelease <name> -n <ns>

# 2. (Optional) Delete the HelmRelease so Flux recreates it on next reconcile
kubectl delete helmrelease <name> -n <ns>

# 3. Reconcile and watch
flux reconcile kustomization <parent-ks> --with-source
kubectl get pods -n <ns> -w
```

Avoid deleting other resources (Deployment, Service) that are owned by the HelmRelease; Flux/Helm will manage them.

---

## PostgreSQL (CNPG)

**Application credentials** (from `-app` secret):

```bash
kubectl get secret -n database <cluster-name>-app -o jsonpath='{.data.username}' | base64 -d
kubectl get secret -n database <cluster-name>-app -o jsonpath='{.data.password}' | base64 -d
```

**Connect with psql:**

```bash
psql -h <cluster-name>-rw.database.svc.cluster.local -U <app-username> -d <app-database-name> -W
```

Use `-app` for application access; reserve `-superuser` for admin.

---

## Nextcloud: database restore

1. **Debug pod with DB access:**

   ```bash
   kubectl run tmp-shell --rm -i --tty --image nicolaka/netshoot -n default -- /bin/bash
   ```

2. **Connect as postgres and fix permissions if needed:**

   ```bash
   psql -h postgres16-rw.database.svc.cluster.local -U postgres -d nextcloud
   # e.g. GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO nextcloud;
   ```

3. **Restore backup:**

   ```bash
   pg_restore -h postgres16-rw.database.svc.cluster.local -U nextcloud -d nextcloud \
     --clean --if-exists --no-owner --no-privileges --no-tablespaces --no-comments \
     <backup-file>.sql
   ```

4. **After restore: data-fingerprint (and optionally turn off maintenance mode):**

   ```bash
   kubectl exec -it <nextcloud-pod> -n default -c nextcloud -- \
     su -s /bin/sh www-data -c "php occ maintenance:data-fingerprint"
   # If you enabled maintenance mode: php occ maintenance:mode --off
   ```

Tip: put Nextcloud in maintenance mode during restore (`php occ maintenance:mode --on` / `--off`).

---

## Talos: network interface speeds

```bash
# One node
talosctl --nodes <node-ip> get links -o yaml | grep -E "id:|speedMbit:|operationalState: up"

# All nodes (script)
for node_ip in 192.168.0.11 192.168.0.12 192.168.0.13; do
  echo "=== Node: $node_ip ==="
  talosctl --nodes "$node_ip" get links -o yaml 2>/dev/null | \
    awk '
      BEGIN { name=""; speed=""; state=""; type="" }
      /^    id:/ { name=$2 }
      /^    type:/ { type=$2 }
      /^    speedMbit:/ { speed=$2 }
      /^    operationalState:/ { state=$2 }
      /^---$/ {
        if (name && state == "up" && type == "ether" && !match(name, /^lxc/)) {
          if (speed == "" || speed == "4294967295") printf "  %-20s %s\n", name, "N/A (virtual/unknown)"
          else printf "  %-20s %s Mbps\n", name, speed
        }
        name=""; speed=""; state=""; type=""
      }
      END {
        if (name && state == "up" && type == "ether" && !match(name, /^lxc/)) {
          if (speed == "" || speed == "4294967295") printf "  %-20s %s\n", name, "N/A (virtual/unknown)"
          else printf "  %-20s %s Mbps\n", name, speed
        }
      }
    ' | sort
  echo ""
done
```

- `speedMbit`: link speed in Mbps; `4294967295` usually means virtual/unknown.
- Physical NICs show real speeds (e.g. 1000, 2500, 10000).
