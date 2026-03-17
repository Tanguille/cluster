---
name: add-app-to-cluster
description: |-
  Deploy new applications to the Kubernetes cluster via FluxCD GitOps. Creates HelmReleases, Kustomizations, and namespace configs following repo conventions.

  user: "Deploy jellyfin" → Create HelmRelease with app-template, HTTPRoute, and persistence
  user: "Add uptime-kuma" → Deploy with probes, route, and resource limits
  user: "Install prometheus exporter" → Create HelmRelease with custom scrape config

  Capabilities: Namespace selection, HelmRelease templating, HTTPRoute setup, validation (kubeconform), Flux reconciliation.

  Use proactively when: User mentions deploying, installing, adding, or setting up any application/service.
---

# Add App to Cluster

Deploy applications to the Kubernetes cluster using FluxCD GitOps patterns.

## When to Delegate to Subagents

**PARALLEL (independent tasks):**

- Research on kubesearch.dev - Spawn subagent with minimal context (app name, namespace preference)
- Multiple unrelated app deployments - Each can be a separate subagent

**SEQUENTIAL (dependencies):**

- Validation → Must complete after file creation
- PR creation → Must complete after validation passes

**INLINE (simple, fast tasks):**

- Single file edits (< 5 lines)
- Reading existing files for reference
- Adding to kustomization.yaml resources list

## Workflow

### 1. Research (Delegate to Subagent)

Spawn a subagent to research the application before proceeding.

```
Spawn subagent with:
- App name
- Preferred namespace (if mentioned)
- Task: Search kubesearch.dev for FluxCD + Helm installations
- Return: Best reference URL + key values to adapt
```

**Subagent context (minimal):**

```yaml
app_name: "<app-name>"
namespace: "<namespace>"  # or ask user
output: Find kubesearch.dev reference + key configuration values
```

### 2. Create Worktree

```bash
git worktree add ../<app-name>-worktree -b feat/add-<app-name>
cd ../<app-name>-worktree
```

### 3. Determine Namespace

Choose based on app purpose:

| Namespace | Purpose |
|-----------|---------|
| `default` | General apps |
| `network` | Proxies, gateways |
| `observability` | Monitoring, metrics |
| `media` | Media servers |
| `database` | Databases |
| `security` | Security tools |

### 4. Create Structure

```
kubernetes/apps/<namespace>/<app-name>/
├── ks.yaml              # Flux Kustomization
└── app/
    ├── helmrelease.yaml
    └── kustomization.yaml
```

### 5. Files

**ks.yaml:**

```yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <app-name>
  namespace: &namespace <namespace>
spec:
  targetNamespace: *namespace
  interval: 30m
  path: ./kubernetes/apps/<namespace>/<app-name>/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  wait: false
```

**app/kustomization.yaml:**

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <namespace>

resources:
  - ./helmrelease.yaml
```

**app/helmrelease.yaml** (app-template):

```yaml
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: <app-name>
spec:
  interval: 30m
  chartRef:
    kind: OCIRepository
    name: app-template

  values:
    controllers:
      <app-name>:
        replicas: 1
        strategy: RollingUpdate

        annotations:
          reloader.stakater.com/auto: "true"

        pod:
          securityContext:
            runAsUser: 1000
            runAsGroup: 1000
            fsGroup: 1000
            fsGroupChangePolicy: "OnRootMismatch"

        containers:
          app:
            image:
              repository: <image-repo>
              tag: <tag>
            resources:
              requests:
                cpu: 5m
                memory: 16Mi
              limits:
                cpu: 500m
                memory: 512Mi

    service:
      app:
        controller: <app-name>
        ports:
          http:
            port: <port>

    route:
      app:
        hostnames:
          - "{{ .Release.Name }}.${SECRET_DOMAIN}"
        parentRefs:
          - name: envoy-internal
            namespace: network
```

### 6. Update Namespace Kustomization

Add to `kubernetes/apps/<namespace>/kustomization.yaml`:

```yaml
resources:
  - ./<app-name>/ks.yaml
```

### 7. Validate

```bash
kubeconform -strict kubernetes/
```

### 8. Create PR

```bash
git add .
git commit -m "feat(<namespace>): add <app-name>"
git push -u origin feat/add-<app-name>
gh pr create --title "feat(<namespace>): add <app-name>" --body "Deploy <app-name> to <namespace> namespace"
```

## Anti-Patterns

**DON'T:**

- Deploy without checking kubesearch.dev for existing configs
- Skip validation before PR
- Use `cd` + commands in bash tool (use `workdir` param)
- Create new namespace without confirming with user
- Hardcode domains (use `${SECRET_DOMAIN}`)

## Quick Reference

| Task | Command |
|------|---------|
| Validate | `kubeconform -strict kubernetes/` |
| Reconcile | `flux reconcile kustomization <name>` |
| Check logs | `kubectl logs -n <ns> deployment/<app>` |

## Related

- git-worktree-isolation skill
- flux-operator AGENTS.md
