# Add-app manifest templates

Adapt paths, namespaces, images, and ports to the target application.

## ks.yaml

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

## app/kustomization.yaml

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <namespace>

resources:
  - ./helmrelease.yaml
```

## app/helmrelease.yaml (app-template)

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
        strategy: RollingUpdate  # use Recreate for RWO ceph-block when needed

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

## Namespace kustomization entry

```yaml
resources:
  - ./<app-name>/ks.yaml
```
