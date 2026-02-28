---
name: add-app-to-cluster
description: |
  Add a new application to the Kubernetes cluster using GitOps with FluxCD. Use when:
  - User asks to add, install, or deploy a new app to the cluster
  - User wants to deploy a Helm chart via Flux
  - Setting up a new service/application in the cluster
---

# Add App to Cluster

This skill adds a new application to the Kubernetes cluster using FluxCD GitOps.

## Workflow

### 1. Research Existing Installations

Before creating from scratch, check **https://kubesearch.dev** for existing FluxCD/Helm installations of the app:

1. Search for the app name on kubesearch.dev
2. Find installations matching our setup style (FluxCD + app-template or similar)
3. Use the closest match as reference/starting point
4. Adapt values to match our patterns (security, resources, routes)

### 2. Create Isolated Worktree

Use the **git-worktree-isolation** skill to create an isolated branch.

### 3. Determine Namespace

Choose or create a namespace based on app purpose:

- `default` - General apps
- `network` - Networking tools (proxies, gateways)
- `observability` - Monitoring, logging, metrics
- `media` - Media servers, storage
- `database` - Databases
- `security` - Security tools
- Or create new namespace folder if needed

### 3. Create App Structure

Create the following structure under `kubernetes/apps/<namespace>/<app-name>/`:

```
<app-name>/
├── ks.yaml              # Flux Kustomization
└── app/
    ├── helmrelease.yaml # Helm release configuration
    └── kustomization.yaml
```

### 4. Create ks.yaml

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

### 5. Create app/kustomization.yaml

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <namespace>

resources:
  - ./helmrelease.yaml
```

### 6. Create helmrelease.yaml

Use the **app-template** chart (recommended for most apps):

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

### 7. Add to Namespace Kustomization

Add to `kubernetes/apps/<namespace>/kustomization.yaml`:

```yaml
resources:
  - ./<app-name>/ks.yaml
```

### 8. Validate

```bash
# Format YAML
yamlfmt -w kubernetes/

# Validate Kubernetes manifests
kubeconform -strict kubernetes/

# Test Flux reconciliation
flux-local test --all-namespaces --enable-helm --path kubernetes/flux/cluster
```

### 9. Create PR

Create a PR with a clear title following Conventional Commits:

```
feat(<namespace>): add <app-name>
```

## Tips

- **Check kubesearch.dev first** - Find existing FluxCD installations to use as reference
- **Follow established patterns** - Don't deviate from existing app structures in the repo
- **Validation required** - Always run validation before creating PR
- **Image source**: Most apps are on Docker Hub or GHCR
- **Port**: Check the app's default port (80, 8080, 3000, etc.)
- **Route**: Remove `route` section if no internal URL needed
- **Persistence**: Add `persistence` section if app needs storage
- **Examples**: See existing apps in `kubernetes/apps/default/` for reference

## Related Skills & Resources

- **git-worktree-isolation** - For creating isolated branches
- **skill-creator** - For best practices on skill design
- **flux-operator AGENTS.md** - Reference: https://github.com/controlplaneio-fluxcd/flux-operator/blob/main/AGENTS.md
