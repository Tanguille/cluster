# litellm → litellm-operator (Postgres / DB mode) migration

Move the litellm gateway from the hand-rolled app-template HelmRelease + file-mode ConfigMap to
the `home-operations/litellm-operator`, running in **DB mode (Postgres)** so models update live
over the admin API and virtual keys / spend tracking / the admin UI become available.

**Strategy:** parallel-then-swap. Stand up an operator-managed proxy (`litellm-next`) beside the
current `litellm`, validate end-to-end, then swap it onto the `litellm` Service and remove the old
release. Database onboarding uses the CNPG-native pattern (managed role + `Database` CR), matching
the repo-wide move off `postgres-init`.

## What this branch adds (`feat/litellm-operator-postgres`)
- `kubernetes/apps/litellm-system/` — new namespace + `litellm-operator` install (OCIRepository
  chart `0.0.4`, HelmRelease; operator image is digest-pinned by the chart). Self-managed
  validating-webhook cert (no cert-manager dep).
- `kubernetes/apps/database/cloudnative-pg/cluster/`:
  - `cluster.yaml` — added `spec.managed.roles` entry `litellm` (login; password from `litellm-db`).
  - `litellm-db-secret.sops.yaml` — basic-auth Secret (role password); kept with the cluster so the
    managed role reconciles with its secret present.
- `kubernetes/apps/database/cloudnative-pg/databases/` — new `cloudnative-pg-databases` Flux
  Kustomization (dependsOn `cloudnative-pg-cluster`) holding `litellm-database.yaml` (`Database`
  CR, owner `litellm`, `databaseReclaimPolicy: retain`). Kept separate from the cluster
  Kustomization (which has `wait: true` and 7 dependents) so a DB hiccup can't cascade.
- `kubernetes/apps/ai/litellm-next/` — `LiteLLMProxy` (`applyMode: api`, DB env, Service port 80,
  temp route `litellm-next.${SECRET_DOMAIN}`) + three `LiteLLMModel`s: `qwen-3.6` thinking-on,
  `qwen-3.6-fast` thinking-off via `extra_body.chat_template_kwargs`, and `qwen3-embedding`
  pointing at the existing LLMKube InferenceService. Own secret carries the **same**
  master/salt/llama keys as the current litellm + the `DATABASE_URL`.

## Process Instructions
- After completing each step, update this file with the current status.
- Pause for user confirmation before proceeding to the next step.
- Suggest the prompt for continuing to the next step.
- After the last step, fold this into existing docs and remove the plan file.
- Every prompt should verify the branch and worktree before doing any work.

## Steps

### 1. Push + reconcile (gate: does the operator image pull?)
Push the branch; Flux installs the operator in `litellm-system`. Confirm the operator Deployment
is Ready and the four CRDs register. This is the Spegel image-pull gate (expected fine — litellm
v1.89.4 and other fresh tags pull). If the operator image won't pull, stop here.

### 2. Database comes up
`cloudnative-pg-cluster` reconciles the managed role + `Database` CR. Verify the `litellm` role
and database exist on `postgres16` (`kubectl cnpg psql postgres16 -- -c '\du' / '\l'`).

### 3. litellm-next proxy comes up (parallel)
`litellm-next` proxy starts, runs Prisma migrations against `postgres16-rw`, and the operator
pushes both models over the admin API. Verify:
- pod Ready, `GET /v1/models` via `litellm-next.ai.svc` lists `qwen-3.6` + `qwen-3.6-fast`.
- thinking-off probe (json_schema) returns non-empty valid JSON, no `reasoning_content`.

### 4. Swap onto the `litellm` Service
Once validated: remove `./litellm` from `ai/kustomization.yaml`, rename the proxy `litellm-next`
→ `litellm` (so the operator owns the `litellm` Service/DNS), give it the real route hostname
`litellm.${SECRET_DOMAIN}`, and move the ServiceMonitor/dashboard. Reconcile; confirm
`litellm.ai.svc.cluster.local` now serves from the operator-managed proxy and CI/Hermes/open-webui
keep working.
- **Minimise the Service gap:** do the old-`litellm` removal and the `litellm-next`→`litellm`
  rename in the **same commit** so one `cluster-apps` reconcile prunes the old Service and the
  operator creates the new one, instead of two sequential reconciles (which leave
  `litellm.ai.svc` resolving to nothing for ~30–60 s). Do the directory rename as a separate
  cosmetic commit afterwards.
- **ServiceMonitor:** add one for the proxy before/at swap (the operator labels its pods
  differently from the old app-template release, so the existing ServiceMonitor won't match) to
  keep Grafana metrics flowing.

### 5. Cleanup
Delete the old `kubernetes/apps/ai/litellm/` app dir and its `litellm-secret` once the swap is
verified. Rename `litellm-next` dir → `litellm`.

## Rollback
Revert the branch (or re-add `./litellm` to `ai/kustomization.yaml`). The `Database` CR uses
`databaseReclaimPolicy: retain`, so the litellm DB survives CR deletion. The old file-mode litellm
is untouched until step 4, so rollback before the swap is a no-op for live traffic.

## Notes / risks
- Operator is **v0.0.4, v1alpha1** (early alpha). Chart versioning is rough (appVersion 0.0.0;
  image digest-pinned). Treat as alpha.
- `cluster.yaml` `managed.roles` is also edited by the repo-wide postgres-init→CR migration —
  coordinate so both roles land in the same list (no conflict; just additive entries).
- `litellm-next` reuses the **same** master/salt keys as the current litellm, so client virtual
  keys keep working across the swap.
- `DATABASE_URL` in the proxy secret is a static connection string; CNPG `managed.roles` supports
  password rotation, but if rotation is ever enabled the litellm role secret must be synced
  manually (cluster-api-proxy `litellm-db-secret` → `litellm-next-secret`). Not enabled today;
  flagged as a known gap.
- `apiAccess.masterKeyRef` is required for `applyMode: api` so the operator can authenticate admin
  API calls. `LITELLM_MASTER_KEY` is also supplied via `spec.env` for the LiteLLM proxy process
  itself; this is intentionally the same secret key, not a second credential.
- LLMKube auto-registration exists in the operator, but is not enabled here: the current embedding
  InferenceService advertises `/v1/embeddings`, while LiteLLM needs `apiBase` at `/v1`. Keep an
  explicit `qwen3-embedding` `LiteLLMModel` until auto-registration trims embedding/rerank paths.
