# Useful Commands for Kubernetes / Talos

Short index for debugging and day-to-day ops. Prefer **GitOps**: change manifests in Git and run `just reconcile` rather than editing resources in-cluster.

For validation, SOPS, and common workflows, see [common operations](../.agents/common-operations.md). For structured troubleshooting, use the [debug-cluster skill](../.agents/skills/debug-cluster/SKILL.md).

---

## Flux / GitOps

```bash
# List recipes (primary entry point)
just

# Pull latest from Git and reconcile (preferred after config changes)
just reconcile

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

Prefixed with `mise exec --` so they use the repo's pinned `talosctl` and `minijinja-cli`. Drop the
prefix only if direnv is active, which puts the same toolchain on `PATH`.

```bash
# Render a node's machine config, and diff it against what the node is running
mise exec -- just talos render-config <node>
mise exec -- just talos diff-node <node> <node-ip>

# Apply config to a node / upgrade node / upgrade Kubernetes
mise exec -- just talos apply-node <node> <node-ip>
mise exec -- just talos upgrade-node <node> <node-ip>
mise exec -- just talos upgrade-k8s
```

Schematics no longer need a separate update step: `just talos schematic-id <node>` resolves the
Image Factory ID at render time and templates it into the installer image. Editing
`talos/schematic.yaml` (or a per-node `talos/nodes/<role>/<node>.schematic.yaml` override) is
enough. These are plain YAML, not templates.

Always run `mise exec -- just talos diff-node <node> <node-ip>` against every node and confirm
`No changes.` before applying.
See [talos/README.md](../talos/README.md) for the layer model.

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

> Full PVC backup/restore procedure: see the [backup-restore skill](../.agents/skills/backup-restore/SKILL.md).

---

## Troubleshooting failed HelmReleases

> Full procedure: see the [debug-cluster skill](../.agents/skills/debug-cluster/SKILL.md).

---

## PostgreSQL (CNPG)
**Connect with psql:**

```bash
psql -h <cluster-name>-rw.database.svc.cluster.local -U <app-username> -d <app-database-name> -W
```

The superuser (`postgres`) password is in the `cloudnative-pg-secret` secret in the database namespace (`spec.superuserSecret`).

---

## Nextcloud: database restore

1. Start a debug pod with database access:

   ```bash
   kubectl run tmp-psql --rm -i --tty --image ghcr.io/cloudnative-pg/postgresql:18.4-standard-trixie -n database -- bash
   ```

2. Connect as `postgres` and fix permissions if needed:

   ```bash
   psql -h postgres16-rw.database.svc.cluster.local -U postgres -d nextcloud
   # e.g. GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO nextcloud;
   ```

3. Restore a dump:

   ```bash
   pg_restore -h postgres16-rw.database.svc.cluster.local -U nextcloud -d nextcloud \
     --clean --if-exists --no-owner --no-privileges --no-tablespaces --no-comments \
     <backup-file>.dump
   ```

   For plain-text `.sql` dumps, pipe the file to `psql` instead.

4. Run the data fingerprint after restoring:

   ```bash
   kubectl exec -it <nextcloud-pod> -n default -c nextcloud -- \
     su -s /bin/sh www-data -c "php occ maintenance:data-fingerprint"
   ```

   Put Nextcloud in maintenance mode during the restore when appropriate (`php occ maintenance:mode --on` / `--off`).

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
- Physical NICs show real speeds (for example, 1000, 2500, or 10000).
