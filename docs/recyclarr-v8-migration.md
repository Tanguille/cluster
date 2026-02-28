# Recyclarr v8 migration (cache → state)

After upgrading to Recyclarr v8, the first run can hang at **"Migrate: Move cache directory to state"** and then **"Initializing Resource Providers"** with endless spinners.

## Cause

- v8 removed `RECYCLARR_APP_DATA`; state/cache must live under `RECYCLARR_CONFIG_DIR`.
- If that env is unset, Recyclarr may use a path that is read-only in the container (e.g. with `readOnlyRootFilesystem: true`), so the migration never completes and initialization can hang.

## Fix applied in this repo

- **RECYCLARR_CONFIG_DIR** is set to `"/config"` in the Recyclarr HelmRelease so all data (config, state, cache) uses the persisted PVC.

## Run the migration once

So that the next scheduled `sync` is not stuck on the migration step, run the migration once with the same config and volumes.

**Option A – One-off pod (same namespace as Recyclarr):**

```bash
# Replace media with your recyclarr namespace if different
kubectl run recyclarr-migrate --rm -it --restart=Never \
  -n media \
  --image=ghcr.io/recyclarr/recyclarr:8.2.0 \
  --overrides='
{
  "spec": {
    "containers": [{
      "name": "recyclarr",
      "image": "ghcr.io/recyclarr/recyclarr:8.2.0",
      "command": ["recyclarr", "migrate"],
      "env": [
        {"name": "RECYCLARR_CONFIG_DIR", "value": "/config"},
        {"name": "RADARR_API_KEY", "valueFrom": {"secretKeyRef": {"name": "recyclarr", "key": "RADARR_API_KEY"}}},
        {"name": "SONARR_API_KEY", "valueFrom": {"secretKeyRef": {"name": "recyclarr", "key": "SONARR_API_KEY"}}}
      ],
      "volumeMounts": [
        {"name": "config", "mountPath": "/config"}
      ]
    }],
    "volumes": [
      {"name": "config", "persistentVolumeClaim": {"claimName": "recyclarr-config"}}
    ]
  }
}
'
```

Adjust `claimName` and secret `name`/keys to match your cluster (e.g. from the Recyclarr CronJob/PVC and the secret that holds the API keys).

**Option B – If you run Recyclarr locally:**

```bash
export RECYCLARR_CONFIG_DIR=/path/to/your/recyclarr/config
recyclarr migrate
```

Then run `recyclarr sync` as usual.

## "Profile/CF already exists" – run state repair once

If sync completes but reports **"cannot be synced because a profile with that name already exists … run: recyclarr state repair --adopt"**, Recyclarr's state doesn't yet own that profile/CF (common after v8 migration). Run adoption once so the next sync is clean.

**In-cluster (same spec as the CronJob):**

```bash
# Create a one-off Job that runs state repair instead of sync
kubectl create job recyclarr-state-repair -n media --from=cronjob/recyclarr --dry-run=client -o yaml \
  | yq '.spec.template.spec.containers[0].args = ["state", "repair", "--adopt"]' \
  | kubectl apply -f -

kubectl logs -f job/recyclarr-state-repair -n media
kubectl delete job recyclarr-state-repair -n media   # optional cleanup
```

**Locally:**

```bash
export RECYCLARR_CONFIG_DIR=/path/to/your/recyclarr/config
recyclarr state repair --adopt
```

Then run sync again (next cron or another manual job); the adoption error should be gone.

## If sync still fails after migration

- **Custom formats not recognized (e.g. “another CF already exists with that name”):** run
  `recyclarr state repair --adopt`
  so Recyclarr re-adopts existing custom formats by name.
- **Stuck on “Initializing Resource Providers”:** ensure the pod can reach GitHub (TRaSH-Guides and config-templates repos). Check logs with `--log debug` if needed.

## References

- [Recyclarr v8.0 upgrade guide](https://recyclarr.dev/guide/upgrade-guide/v8.0/) (env vars, `repositories` → `resource_providers`, etc.)
- [recyclarr migrate](https://recyclarr.dev/cli/migrate/) – runs migration steps between major versions
- [State / cache](https://recyclarr.dev/guide/troubleshooting/state/) – `state repair --adopt` for relocating instances or adopting existing CFs
