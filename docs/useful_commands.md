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
flux get kustomizations -A
flux get helmreleases -A
```

---

## Talos

Machine configs are rendered on demand and pushed by hand; nothing applies them automatically.
See [talos/README.md](../talos/README.md) for the layer model, the common recipes, and the
toolchain the bare `just` commands assume.

---

## PostgreSQL (CNPG)

**Connect with psql:**

```bash
psql -h <cluster-name>-rw.database.svc.cluster.local -U <app-username> -d <app-database-name> -W
```

The superuser (`postgres`) password is in the `cloudnative-pg-secret` secret in the database namespace (`spec.superuserSecret`).

**Roll a change CNPG's pod diff ignores** (e.g. `priorityClassName`): Flux strips a kubectl-set annotation within minutes, so suspend around it.

```bash
flux suspend ks cloudnative-pg-cluster -n database
kubectl -n database annotate cluster postgres16 --overwrite kubectl.kubernetes.io/restartedAt="$(date -u +%FT%TZ)"
kubectl -n database get cluster postgres16 -w   # wait for the phase to leave, then return to "Cluster in healthy state"
flux resume ks cloudnative-pg-cluster -n database
```

- The phase reads healthy until the operator starts the roll, so wait for it to change first.
- While suspended, any commit merged in the window is held back, and `cloudnative-pg-databases` and its dependents report a stale dependency until the resume. Keep the window short.
- If the phase does not return to healthy within minutes, check `.status.instancesReportedState`, resume anyway and fix the instance.
- Bumping the annotation in `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml` needs no suspend. Once it lives there, always restart through Git, or resuming Flux restores the older value and rolls again.

---

## Nextcloud: database restore

1. Start a debug pod with database access:

   ```bash
   kubectl run tmp-psql --rm -i --tty --image "$(kubectl -n database get cluster postgres16 -o jsonpath='{.spec.imageName}')" -n database -- bash
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

## Orbit: first run, MCP and GitHub

`orbit.<domain>` is LAN-only (`envoy-internal`). The only public path is the GitHub webhook, on its own host `orbit-webhook.<domain>` (`envoy-external`, exact path `/api/v1/integrations/github/webhook`). Data is in the `orbit` database on CNPG, attachments and Orbit's pre-migration backups on the 5Gi `orbit` PVC (kopiur); a restore needs both from the same point in time. Upstream reference: [operations](https://github.com/coollabsio/orbit/blob/main/docs/operations.md), [MCP](https://github.com/coollabsio/orbit/blob/main/docs/mcp.md), [GitHub](https://github.com/coollabsio/orbit/blob/main/docs/github.md).

1. Claim the root account right after the first deploy. The one-time setup URL is logged once, by the first pod, and whoever opens it becomes root:

   ```bash
   kubectl -n default logs deploy/orbit | grep "Initial setup URL"
   ```

   The token lasts 30 minutes. Missed it: `kubectl -n default exec deploy/orbit -- /orbit setup-token rotate` prints a new URL while no root exists yet.

2. Registration is closed (`ORBIT_OPEN_REGISTRATION=false`); people join by invitation from **Admin → Users**.
3. Outgoing mail goes through `smtp-relay` (`ORBIT_SMTP_*`), so **Admin → Email** is read-only. Check it there with **Send test email**.
4. `ORBIT_APP_KEY` (`orbit-secret`) encrypts saved secrets, 2FA and Orbit's own backups. Never rotate it; keep a copy in Vaultwarden.
5. MCP: clients use `https://orbit.<domain>/mcp` (Streamable HTTP). The URL must be the public origin, Orbit checks `Host`.
   - Cluster agents: the ToolHive `orbit` entry (`kubernetes/apps/ai/toolhive/mcp/orbit.yaml`) puts Orbit's tools (`orbit_*`) in the unified vMCP. `ORBIT_MCP_TOKEN` in `toolhive-secrets` holds the full `Bearer <token>` header of a personal API token (docs and chat need a personal token, service-account tokens cover tasks only). To rotate it, create a new token in **Settings → API tokens** and update the secret.
   - Hermes daily task report: [orbit_report.py](../kubernetes/apps/ai/hermes/app/resources/orbit_report.py) reads open tasks, their statuses and the latest replies through the same gateway. After the script is mounted and the live change is approved, set `script: orbit_report.py` and `enabled_toolsets: []` together on the existing `orbit-maintain` job. Keep its prompt, schedule and delivery. The model summarizes the script output with no callable tools. To roll back, restore the job's previous script and toolset values.
   - Interactive clients: OAuth, for example `claude mcp add --scope user --transport http orbit https://orbit.<domain>/mcp`, then authenticate in `/mcp`.

### GitHub integration

One way: a GitHub issue or pull request with the project's label becomes a task. Orbit learns about installations only from GitHub's `installation` webhooks, so the order matters:

1. Cloudflare: the "Allow GitHub webhooks, badges, Matrix" skip rule (dashboard, not in git) needs a clause for `orbit-webhook.<domain>` with that exact path, next to the other webhook hosts. Without it the non-EU block drops GitHub's deliveries.
2. In **Settings → GitHub**, register the App (from `https://orbit.<domain>`). Then set the App's **Webhook URL** to `https://orbit-webhook.<domain>/api/v1/integrations/github/webhook` (GitHub, Developer settings, GitHub Apps, the app, General). Orbit registers the host you browse from, so the default points at the LAN-only host.
3. Install the App on the repositories. If Orbit still says "No repositories installed yet", the install event was missed: redeliver it from the App's **Advanced → Recent deliveries**, or re-save the installation.
4. Connect each project to one repository and a label (create the label in GitHub). The project's **GitHub** settings page does it; the API route is `PUT /api/v1/workspaces/{workspace}/projects/{project}/github` with `{installation_id, repository, label}`, using a session bearer from `POST /api/v1/auth/login` (a request without an `Origin` header returns `session_token`). API tokens do not work on `/api/v1`.

Checks: Cloudflare firewall analytics shows `skip` for `orbit-webhook`, and Orbit answers `200` to `installation` events and `202` to events it ignores. A `202` on every delivery means no installation recorded yet, or no project connected to that repository.

Never attach the UI route to `envoy-external`, and keep the webhook on its own host. k8s-gateway answers a host that sits on both gateways with both IPs, so half the clients land on `envoy-external`, which 404s the UI and `/mcp` (the unified vMCP marked the Orbit backend degraded when this happened).

Postgres: Orbit connects straight to `postgres16-rw` without TLS, because 0.4.1 is built without Postgres TLS ([coollabsio/orbit#155](https://github.com/coollabsio/orbit/issues/155)) and the poolers require it. `pg_stat_ssl` shows all of its connections as plaintext. A `hostnossl ... reject` rule in `pg_hba` would lock it out, and Orbit exits (the pod restarts) when its lock connection drops, for example on a failover. The `orbit` database is its own `Database` manifest: Orbit only accepts builtin `C.UTF-8`.

Every request must arrive through Envoy: Orbit answers `400 https_required` to plain HTTP on the Service, so a `kubectl port-forward` does not work. `orbit-ingress-deny` also blocks every pod but `envoy-internal` and `envoy-external` from port 8080, in-cluster agents included: they use the public URL.

---

## Talos: network interface speeds

```bash
# One node
talosctl --nodes <node-ip> get links -o yaml | grep -E "id:|speedMbit:|operationalState: up"

# All nodes (script)
for node_ip in $(just talos node-ips); do
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

## Hermes cron delivery failures

Use the `profile` and `id` labels from `HermesCronDeliveryFailed`. Match the run time in the execution ledger, then check the same execution in the profile’s outbox:

```bash
read -rp 'Profile and job id: ' profile job
home_dir=/opt/data
[[ $profile == default ]] || home_dir="/opt/data/profiles/$profile"
hermes_exec=(kubectl -n ai exec deploy/hermes -c app -- /command/s6-setuidgid hermes)
"${hermes_exec[@]}" hermes -p "$profile" cron runs "$job" --limit 5
read -rp 'Execution id: ' execution
"${hermes_exec[@]}" /opt/hermes/.venv/bin/python -c '
import sqlite3, sys
db = sqlite3.connect("file:" + sys.argv[1] + "/cron/deliveries.db?mode=ro", uri=True)
print(db.execute("SELECT status FROM deliveries WHERE execution_id=? UNION ALL SELECT terminal_status FROM delivery_tombstones WHERE execution_id=?", (sys.argv[2], sys.argv[2])).fetchall())' "$home_dir" "$execution"
```

A completed execution can have a failed delivery. If the gateway reports `not configured/enabled`, check the connected Discord adapter, the profile route for the exact thread and parent, and the bot’s thread membership and **Send Messages in Threads** permission. An unknown delivery may already have reached Discord. Observe the next scheduled run to avoid posting a duplicate.
