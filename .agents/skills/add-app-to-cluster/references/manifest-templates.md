# Add-app manifest templates

Adapt paths, namespaces, images, and ports to the target application.

## ks.yaml

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app <app-name>
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
  # dependsOn:
  #   - name: <other-app>
  # stateful apps: uncomment for kopiur-backed persistence — full var list in
  # .agents/skills/backup-restore/references/restore-pvc.md#enable-backups-for-an-app
  # components:
  #   - ../../../../components/kopiur
  # postBuild:
  #   substitute:
  #     APP: *app
  #     PVC_CAPACITY: <size>
```

## app/kustomization.yaml

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
```

## app/helmrelease.yaml (app-template)

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app <app-name>
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

        containers:
          app:
            image:
              repository: <image-repo>
              tag: <tag>  # plain upstream tag; Renovate pins the digest
            resources:
              requests:
                cpu: 5m
                memory: 16Mi
              limits:
                cpu: 500m
                memory: 512Mi
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities: { drop: ["ALL"] }

    defaultPodOptions:
      securityContext:
        # match the image's expected uid (568 = home-operations convention)
        runAsNonRoot: true
        runAsUser: 568
        runAsGroup: 568
        fsGroup: 568
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile: { type: RuntimeDefault }

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

    persistence:
      # config:
      #   existingClaim: *app  # pairs with the kopiur component in ks.yaml
      tmp:
        type: emptyDir  # readOnlyRootFilesystem crashes many apps without a writable /tmp
```

## Optional: sops secret

Create `app/secret.sops.yaml` (Secret named `<app-name>-secret`), encrypt with sops, then wire it:

```yaml
# app/kustomization.yaml
resources:
  - helmrelease.yaml
  - secret.sops.yaml
```

```yaml
# helmrelease.yaml, under containers.app
            envFrom:
              - secretRef:
                  name: <app-name>-secret
```

## Optional: configMapGenerator (config files)

```yaml
# app/kustomization.yaml
configMapGenerator:
  - name: <app-name>-configmap
    files:
      - config/<file>.yaml
    options:
      annotations:
        # only when the config contains literal ${VAR} — Flux postBuild substitutes it away otherwise
        kustomize.toolkit.fluxcd.io/substitute: disabled
generatorOptions:
  disableNameSuffixHash: true
```

## Namespace kustomization entry

```yaml
resources:
  - ./<app-name>/ks.yaml
```
