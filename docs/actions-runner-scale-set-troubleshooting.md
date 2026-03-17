# Actions Runner Scale Set – No Runners When Jobs Are Queued

When workflows use `runs-on: cluster-runner` but no runner pods are created, check the following in order.

## 1. Listener is running and connected

The listener receives "Job Available" from GitHub and patches the EphemeralRunnerSet to scale up.

```bash
# Listener pod (name contains "listener")
kubectl get pods -n actions-runner-system -l app.kubernetes.io/name=cluster-runner

# Listener logs – look for "Starting listener", "refreshing token", and any errors
kubectl logs -n actions-runner-system -l app.kubernetes.io/component=listener --tail=100
```

- If you see `githubConfigUrl": "https://github.com/tanguille"` (no `/cluster`), the scale set is still using the org URL and will 404; fix the HelmRelease values and re-apply (see main runner docs).
- If you see "job available" or "job assigned" in logs but no runner pods, the problem is downstream (patch or controller).

## 2. GitHub App permissions (repo-level)

After using a **repo-level** `githubConfigUrl` (`https://github.com/tanguille/cluster`), the GitHub App must be **installed on that repo** with:

- **Actions: Read and write** (so the scale set can see and receive jobs).

Check: repo → Settings → Integrations → GitHub Apps → your app → Configure → ensure it’s installed on `tanguille/cluster` and has Actions permission.

## 3. EphemeralRunnerSet and scaling

The listener patches the EphemeralRunnerSet; the controller creates EphemeralRunners and runner pods.

```bash
# EphemeralRunnerSet – check desiredReplicas and status
kubectl get ephemeralrunnerset -n actions-runner-system
kubectl describe ephemeralrunnerset cluster-runner -n actions-runner-system

# EphemeralRunner resources (runner pods created from these)
kubectl get ephemeralrunners -n actions-runner-system
kubectl get pods -n actions-runner-system -l actions.github.com/scale-set-name=cluster-runner
```

- If `EphemeralRunnerSet` has `desiredReplicas > 0` but no `EphemeralRunner` or pods, check controller logs and RBAC.
- If there are no events or the set is stuck, check listener RBAC (patch `ephemeralrunnersets`) and controller logs.

## 4. Controller logs

```bash
kubectl logs -n actions-runner-system -l app.kubernetes.io/name=actions-runner-controller --tail=100
```

Look for errors creating pods, PVCs, or updating EphemeralRunners.

## 5. PVC / scheduling (kubernetes mode)

Runners use a PVC (`openebs-hostpath`, 25Gi). If PVCs are pending, runner pods won’t start.

```bash
kubectl get pvc -n actions-runner-system
kubectl describe pvc -n actions-runner-system
```

## 6. Workflow `runs-on` must match scale set name

Scale set name defaults to the Helm release name: **cluster-runner**. Workflows must use:

```yaml
runs-on: cluster-runner
```

If you use a different release name, set `runnerScaleSetName` in the Helm values to the same value you use in `runs-on`.

## Quick checklist

| Check | Command / place |
|-------|------------------|
| Listener running | `kubectl get pods -n actions-runner-system -l app.kubernetes.io/component=listener` |
| Listener logs | `kubectl logs -n actions-runner-system -l app.kubernetes.io/component=listener --tail=100` |
| GitHub config URL in logs | Should be `https://github.com/tanguille/cluster` |
| App installed on repo | Repo Settings → Integrations → GitHub Apps |
| EphemeralRunnerSet | `kubectl get ephemeralrunnerset -n actions-runner-system` |
| Runner pods | `kubectl get pods -n actions-runner-system -l actions.github.com/scale-set-name=cluster-runner` |
| PVCs | `kubectl get pvc -n actions-runner-system` |
| `runs-on` in workflow | Must be `cluster-runner` |
