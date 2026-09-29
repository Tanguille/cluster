# Paperclip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Paperclip AI agent orchestration (operator `0.19.1` + Instance `my-paperclip`) into namespace `ai` via FluxCD GitOps.

**Architecture:** Two Flux Kustomizations (litellm pattern): `paperclip-operator` reconciles `app/` (OCIRepository + HelmRelease for the operator); `paperclip` reconciles `instance/` (Instance CR + SOPS Secrets + internal HTTPRoute) with `dependsOn: paperclip-operator`. Private ClusterIP + envoy-internal HTTPRoute; managed PostgreSQL; LiteLLM passthrough for LLM keys.

**Tech Stack:** FluxCD (OCIRepository/HelmRelease/Kustomization), `paperclip-operator` chart `0.19.1`, app image `ghcr.io/paperclipai/paperclip:v2026.916.1`, SOPS/age, Gateway API HTTPRoute, `ceph-block` storage, VictoriaMetrics ServiceMonitor.

---

## File Structure

| File | Responsibility |
|---|---|
| `kubernetes/apps/ai/paperclip/app/ocirepository.yaml` | Pins operator chart `0.19.1` (Renovate-tracked) |
| `kubernetes/apps/ai/paperclip/app/helmrelease.yaml` | Installs operator + CRDs into `ai` |
| `kubernetes/apps/ai/paperclip/app/kustomization.yaml` | Joins operator resources |
| `kubernetes/apps/ai/paperclip/instance/instance.yaml` | `Instance/my-paperclip` desired state (managed DB, private, LiteLLM) |
| `kubernetes/apps/ai/paperclip/instance/secret.sops.yaml` | `paperclip-auth` + `paperclip-api-keys` (SOPS-encrypted, never plaintext) |
| `kubernetes/apps/ai/paperclip/instance/httproute.yaml` | Internal `paperclip.${SECRET_DOMAIN}` → `my-paperclip:3100` |
| `kubernetes/apps/ai/paperclip/instance/kustomization.yaml` | Joins instance resources |
| `kubernetes/apps/ai/paperclip/ks.yaml` | Two Kustomizations with `dependsOn` ordering |
| `kubernetes/apps/ai/kustomization.yaml` | Registers `./paperclip/ks.yaml` (modify, one line) |

---

### Task 1: Scaffold directories + OCIRepository

**Files:**
- Create: `kubernetes/apps/ai/paperclip/app/ocirepository.yaml`

- [ ] **Step 1: Create directories**

Run: `mkdir -p kubernetes/apps/ai/paperclip/app kubernetes/apps/ai/paperclip/instance`
Expected: exit 0, no output.

- [ ] **Step 2: Write ocirepository.yaml**

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/source.toolkit.fluxcd.io/ocirepository_v1.json
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: paperclip-operator
spec:
  interval: 1h
  layerSelector:
    mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
    operation: copy
  ref:
    tag: 0.19.1
  url: oci://ghcr.io/paperclipinc/charts/paperclip-operator
```

- [ ] **Step 3: Validate YAML parses**

Run: `python3 -c "import yaml; list(yaml.safe_load_all(open('kubernetes/apps/ai/paperclip/app/ocirepository.yaml'))); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/ai/paperclip/app/ocirepository.yaml
git commit -m "feat(paperclip): add operator OCIRepository 0.19.1"
```

---

### Task 2: Operator HelmRelease

**Files:**
- Create: `kubernetes/apps/ai/paperclip/app/helmrelease.yaml`

- [ ] **Step 1: Write helmrelease.yaml**

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/helm.toolkit.fluxcd.io/helmrelease_v2.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: paperclip-operator
spec:
  interval: 30m
  chartRef:
    kind: OCIRepository
    name: paperclip-operator
  values:
    crds:
      install: true
      keep: true
    metrics:
      enabled: true
      serviceMonitor:
        enabled: true
        interval: 30s
    # Disabled for Cilium compat (same class of issue as kguardian broker:
    # rendered default-deny policy blackholes ClusterIP egress). Re-enable
    # only after verifying pod-to-DB/DNS with it on.
    networkPolicy:
      enabled: false
    leaderElection:
      enabled: true
```

- [ ] **Step 2: Verify chart default compatibility**

Run: `helm show values oci://ghcr.io/paperclipinc/charts/paperclip-operator --version 0.19.1 | grep -E "^(replicaCount|leaderElection|crds|metrics|networkPolicy)" `
Expected: keys present (values render; exact grep hits may vary, exit 0 on `helm show` success).

- [ ] **Step 3: Commit**

```bash
git add kubernetes/apps/ai/paperclip/app/helmrelease.yaml
git commit -m "feat(paperclip): add operator HelmRelease with CRDs and ServiceMonitor"
```

---

### Task 3: Operator kustomization + build check

**Files:**
- Create: `kubernetes/apps/ai/paperclip/app/kustomization.yaml`

- [ ] **Step 1: Write app/kustomization.yaml**

```yaml
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ocirepository.yaml
  - helmrelease.yaml
```

- [ ] **Step 2: Build operator kustomization**

Run: `kustomize build ./kubernetes/apps/ai/paperclip/app`
Expected: renders OCIRepository + HelmRelease documents, exit 0 (use `mise x -- kustomize build` if binary missing).

- [ ] **Step 3: Commit**

```bash
git add kubernetes/apps/ai/paperclip/app/kustomization.yaml
git commit -m "feat(paperclip): join operator resources with kustomization"
```

---

### Task 4: Instance CR

**Files:**
- Create: `kubernetes/apps/ai/paperclip/instance/instance.yaml`

- [ ] **Step 1: Write instance.yaml**

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/paperclipinc/paperclip-operator/main/config/crd/bases/paperclip.inc_instances.yaml
apiVersion: paperclip.inc/v1alpha1
kind: Instance
metadata:
  name: my-paperclip
  namespace: ai
spec:
  image:
    repository: ghcr.io/paperclipai/paperclip
    tag: v2026.916.1
    pullPolicy: IfNotPresent
  workload: StatefulSet
  deployment:
    mode: authenticated
    exposure: private
  database:
    mode: managed
    managed:
      storageSize: 10Gi
      storageClass: ceph-block
  auth:
    secretRef:
      name: paperclip-auth
      key: BETTER_AUTH_SECRET
  adapters:
    apiKeysSecretRef:
      name: paperclip-api-keys
  storage:
    persistence:
      enabled: true
      size: 5Gi
      storageClass: ceph-block
      accessModes:
        - ReadWriteOnce
  networking:
    service:
      type: ClusterIP
      port: 3100
  security:
    seLinuxRelabel: false
    networkPolicy:
      enabled: false
  probes:
    type: auto
    startup:
      failureThreshold: 60
      periodSeconds: 5
  availability:
    replicas: 1
  podAnnotations:
    reloader.stakater.com/auto: "true"
  resources:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: "2"
      memory: 2Gi
  observability:
    metrics:
      enabled: true
      serviceMonitor:
        enabled: true
        interval: 30s
    logging:
      level: info
```

- [ ] **Step 2: Confirm CRD field names against chart**

Run: `helm template paperclip-operator oci://ghcr.io/paperclipinc/charts/paperclip-operator --version 0.19.1 --include-crds 2>/dev/null | grep -E "seLinuxRelabel|apiKeysSecretRef|exposure:" | head -10`
Expected: at least `apiKeysSecretRef` and `exposure` hits; if `seLinuxRelabel` absent, remove that block from instance.yaml before proceeding (chart/CRD skew).

- [ ] **Step 3: Commit**

```bash
git add kubernetes/apps/ai/paperclip/instance/instance.yaml
git commit -m "feat(paperclip): add my-paperclip Instance (managed DB, private)"
```

---

### Task 5: SOPS secrets (ask-first gate)

**Files:**
- Create: `kubernetes/apps/ai/paperclip/instance/secret.sops.yaml`

> Gate: creating a new SOPS secret touches `age.key`. Generate values first, show the operator the exact encrypt command, and proceed only after explicit approval. Never commit plaintext.

- [ ] **Step 1: Generate BETTER_AUTH_SECRET (in memory, do not save)**

Run: `openssl rand -hex 32`
Expected: 64-char hex string on stdout. Copy it for Step 3; do not write to disk.

- [ ] **Step 2: Read LiteLLM key source (no decrypt of other apps)**

Run: `kubectl get secret litellm -n ai -o jsonpath='{.metadata.name}' 2>&1 || echo "LITELLM-SECRET-ABSENT"`
Expected: either secret name or `LITELLM-SECRET-ABSENT`. If absent, stop and ask the user for the LLM key source before continuing.

- [ ] **Step 3: Write plaintext staging file (0600, deleted in Step 5)**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: paperclip-auth
  namespace: ai
stringData:
  BETTER_AUTH_SECRET: <PASTE-64-HEX-FROM-STEP-1>
---
apiVersion: v1
kind: Secret
metadata:
  name: paperclip-api-keys
  namespace: ai
stringData:
  ANTHROPIC_API_KEY: <PASTE-FROM-LITELLM-SOURCE>
  OPENAI_API_KEY: <PASTE-FROM-LITELLM-SOURCE>
  OPENAI_BASE_URL: http://litellm.ai.svc.cluster.local/v1
```

Write to: `/tmp/paperclip-secrets.yaml` with `chmod 600`.

- [ ] **Step 4: Encrypt in place with repo SOPS config**

Run: `sops --encrypt --in-place /tmp/paperclip-secrets.yaml && head -8 /tmp/paperclip-secrets.yaml`
Expected: `stringData` values replaced with `ENC[AES256_GCM,...]` entries plus a trailing `sops:` block.

- [ ] **Step 5: Install encrypted file and shred plaintext traces**

Run: `cp /tmp/paperclip-secrets.yaml kubernetes/apps/ai/paperclip/instance/secret.sops.yaml && shred -u /tmp/paperclip-secrets.yaml && grep -c "ENC\[AES256_GCM" kubernetes/apps/ai/paperclip/instance/secret.sops.yaml`
Expected: count `3` (one per secret value; `OPENAI_BASE_URL` stays plaintext as it is not sensitive — count may be `3`, adjust grep expectation to `>=3`).

- [ ] **Step 6: Commit encrypted secret only**

```bash
git add kubernetes/apps/ai/paperclip/instance/secret.sops.yaml
git commit -m "feat(paperclip): add SOPS auth and LLM key secrets"
```

---

### Task 6: HTTPRoute + instance kustomization

**Files:**
- Create: `kubernetes/apps/ai/paperclip/instance/httproute.yaml`
- Create: `kubernetes/apps/ai/paperclip/instance/kustomization.yaml`

- [ ] **Step 1: Write httproute.yaml**

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/gateway.networking.k8s.io/httproute_v1.json
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: paperclip
  namespace: ai
spec:
  hostnames:
    - paperclip.${SECRET_DOMAIN}
  parentRefs:
    - name: envoy-internal
      namespace: network
      sectionName: https
  rules:
    - backendRefs:
        - name: my-paperclip
          port: 3100
```

- [ ] **Step 2: Write instance/kustomization.yaml**

```yaml
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - instance.yaml
  - secret.sops.yaml
  - httproute.yaml
```

- [ ] **Step 3: Build instance kustomization (SOPS-encrypted secret renders as ciphertext — expected)**

Run: `kustomize build ./kubernetes/apps/ai/paperclip/instance`
Expected: renders Instance + 2 Secrets (ciphertext) + HTTPRoute, exit 0.

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/ai/paperclip/instance/httproute.yaml kubernetes/apps/ai/paperclip/instance/kustomization.yaml
git commit -m "feat(paperclip): add internal HTTPRoute and instance kustomization"
```

---

### Task 7: Flux Kustomizations + namespace registration

**Files:**
- Create: `kubernetes/apps/ai/paperclip/ks.yaml`
- Modify: `kubernetes/apps/ai/kustomization.yaml` (add one line)

- [ ] **Step 1: Write ks.yaml**

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: paperclip-operator
  namespace: &namespace ai
spec:
  targetNamespace: *namespace
  path: ./kubernetes/apps/ai/paperclip/app
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  prune: true
  wait: true
  interval: 30m
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: paperclip
  namespace: &namespace ai
spec:
  targetNamespace: *namespace
  dependsOn:
    - name: paperclip-operator
  path: ./kubernetes/apps/ai/paperclip/instance
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  prune: true
  wait: true
  interval: 30m
```

- [ ] **Step 2: Register in ai/kustomization.yaml**

Old string (`kubernetes/apps/ai/kustomization.yaml`):
```yaml
  - ./opencode/ks.yaml
  - ./toolhive/ks.yaml
```
New string:
```yaml
  - ./opencode/ks.yaml
  - ./paperclip/ks.yaml
  - ./toolhive/ks.yaml
```

- [ ] **Step 3: Commit**

```bash
git add kubernetes/apps/ai/paperclip/ks.yaml kubernetes/apps/ai/kustomization.yaml
git commit -m "feat(paperclip): wire Flux Kustomizations with operator ordering"
```

---

### Task 8: Full validation gate

**Files:** none (verification only)

- [ ] **Step 1: Build both kustomizations**

Run: `kustomize build ./kubernetes/apps/ai/paperclip/app > /dev/null && echo APP-OK; kustomize build ./kubernetes/apps/ai/paperclip/instance > /dev/null && echo INSTANCE-OK`
Expected: `APP-OK` then `INSTANCE-OK`.

- [ ] **Step 2: Run repo validation script**

Run: `bash .agents/skills/pr-review/scripts/validate-pr.sh`
Expected: exit 0. If failures reference only pre-existing dirty files outside `kubernetes/apps/ai/paperclip/`, note them and continue; if failures reference new paperclip files, fix before proceeding.

- [ ] **Step 3: Flux dry-run diff (no apply — ask-first for live reconcile)**

Run: `flux diff kustomization paperclip-operator --path ./kubernetes/apps/ai/paperclip/app 2>&1 | head -40 || true`
Expected: diff output or "no changes" — read-only, no cluster mutation.

---

### Task 9: PR (no push without approval)

**Files:** none (git operation only)

- [ ] **Step 1: Review scope hygiene**

Run: `git status --short && echo "---" && git log --oneline -9 | head -12`
Expected: only `kubernetes/apps/ai/paperclip/**` + `kubernetes/apps/ai/kustomization.yaml` in the new commits; pre-existing dirty files (`.mise.toml`, `database/*`, `kritik/*`) untouched by this work.

- [ ] **Step 2: Stop and ask for push approval**

Post the commit list and ask the user for explicit push/PR approval per repo policy (never push to `main` unasked; SOPS secrets already encrypted at rest). Do not run `git push` in this plan.
