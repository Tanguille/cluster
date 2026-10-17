# SOPS operations and recovery

This is a public GitOps repository: only encrypted secret payloads belong in Git.
Never commit private identities, plaintext exports, or `age.key*` backups. Reading
private keys, decrypting/editing encrypted files, applying/reconciling, and pushing
require explicit approval. This runbook is guidance, not authorization to do them.

## Current configuration and namespace scope

- [`.sops.yaml`](../.sops.yaml) encrypts all values in `talos/*.sops.yaml`
  with a classical age recipient; `bootstrap/` and `kubernetes/` use a PQ age
  recipient and encrypt `data`/`stringData` values, leaving resource metadata public.
- [`.mise.toml`](../.mise.toml) points `SOPS_AGE_KEY_FILE` at the local `age.key`.
  Preserve **both classical and PQ identities** in that file and in recovery
  backups. A classical-only copy can decrypt Talos but fails on Kubernetes; see
  [common operations](../.agents/common-operations.md#secrets-management-sops).
- [`cluster-apps`](../kubernetes/flux/cluster/ks.yaml) is in `flux-system` and
  uses `spec.decryption.secretRef.name: sops-age`. It renders `kubernetes/apps`
  and patches each child Flux Kustomization with the same explicit decryption ref.
- Each namespace's Kustomize overlay sets `namespace:` and includes
  [`components/common`](../kubernetes/components/common/kustomization.yaml).
  It transforms **child Flux Kustomizations as well as Secrets** into that
  namespace. For example, rendered `karakeep` is in `default`, and its key lookup
  is `default/sops-age`, not `flux-system/sops-age`. `targetNamespace` controls
  the child's output, not the namespace used for its decryption Secret lookup.
- The common component includes the encrypted `sops-age` Secret in each namespace.
  The [controller configuration](../kubernetes/apps/flux-system/flux-instance/app/helmrelease.yaml)
  also sets `--sops-age-secret=sops-age`. That global facility does not justify
  removing the namespace-local Secrets while explicit child refs remain.

**Do not centralize the key by removing it from the common component.** That would
break the current explicit lookup chain. Any future global-only design needs a
separate migration, rendered proof, and recovery plan. Flux already decrypts SOPS;
no additional secrets operator is needed. Splitting recipients is deferred:
the same controller holding all private keys does not isolate a controller compromise.

To verify the namespace chain locally without contacting Kubernetes or decrypting:

```bash
set -euo pipefail
mise exec -- flux build kustomization cluster-apps \
  --path ./kubernetes/apps --kustomization-file ./kubernetes/flux/cluster/ks.yaml \
  --dry-run --kubeconfig /dev/null |
  mise exec -- yq 'select(.kind == "Kustomization" and .apiVersion == "kustomize.toolkit.fluxcd.io/v1") |
    {"name": .metadata.name, "namespace": .metadata.namespace, "key": .spec.decryption.secretRef.name}' -
```

## Guardrails and safe checks

Run from the repository with Python 3 and mise available (the regression suite
also uses the repository-pinned Lefthook to exercise formatter/re-staging order):

```bash
mise exec -- bash scripts/secret-check.sh --staged
mise exec -- bash scripts/secret-check.sh --all
mise exec -- python3 -m unittest discover -s scripts/tests -v
mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh
```

The pre-commit check runs **after** formatters finish re-staging. It reads changed
Git **index blobs**, not worktree files, so an unstaged encrypted replacement cannot
hide staged plaintext. `--all` checks every tracked index blob, including unchanged
files; in CI that index is the checked-out commit. Neither mode opens untracked or
ignored files, follows worktree symlinks, reads `age.key`, or decrypts anything.
Root `.sops.yaml` is policy, not an encrypted payload. Run `git add` only on reviewed
intended files before relying on index checks for new changes.

Checks reject `age.key*` filenames before reading their contents, classical/PQ/plugin
age identity tokens and private-key headers in arbitrary blobs, malformed or missing
SOPS metadata, and plaintext scalar values under `data`/`stringData` in `.sops.yaml`
or `.sops.yml` payloads. Talos instead requires whole-document encrypted values.
YAML aliases, multiple documents, and duplicate keys are checked structurally with
the pinned `yq`; comments and public resource metadata are not secret values.
Diagnostics print paths and fixed reasons only, never values or parser input.
Blobs over 10 MiB fail closed rather than being silently skipped.

These are bounded accident-prevention checks, **not cryptographic verification**
of ciphertext/MACs, a general plaintext-secret detector, or a Git-history audit.
They do not detect every encoding or arbitrary credentials outside SOPS files.
Gitleaks remains complementary CI coverage. Never bypass a failure by renaming a
secret or adding an allowlist; encrypt it in an approved workflow instead.

## Human-held backup and recovery

The owner should keep a verified, access-controlled backup of the complete dual
identity bundle in **Vaultwarden**, with recovery instructions and independent
access that does not depend on this cluster working. This is human-held backup:
do not upload keys automatically or store them in CI variables, logs, or this repo.
Test recovery on an approved trusted workstation for one Talos and one Kubernetes
file, without printing plaintext. Confirm the pinned tools support PQ age.

On loss of local access, obtain approval, recover both identities from Vaultwarden
to a protected local file (restrict permissions to the owner), and verify both
recipient families before doing anything live. Do not substitute a classical-only
backup. If Flux cannot decrypt, inspect Kustomization status and namespace/key-ref
metadata without fetching Secret values. Restore the appropriate namespace-local
key bundle through an approved recovery procedure. For a fresh/lost cluster, the
initial `flux-system/sops-age` must be provisioned securely out of band: an encrypted
copy of the key cannot bootstrap its own decryption. Preserve the `.agekey` data-key
suffix Flux expects when provisioning the Secret; do not put plaintext manifests in Git.

## Coordinated rotation (approval and separate change required)

`sops updatekeys` updates the recipients wrapping the **existing** data key according
to `.sops.yaml`; it does not refresh that data key. `sops rotate` generates a fresh
data key and re-encrypts values. **Neither revokes decryptability of historical Git
blobs.** A compromised identity may have exposed all historical application secrets:
rotate the actual passwords/tokens/certificates at their issuers too.

Use an overlap migration, not a sequential switch of the only key:

1. Inventory both recipient families and all key consumers, including bootstrap,
   Talos recovery, `flux-system`, and every rendered child namespace. Generate new
   identities on an approved trusted workstation and back up both old and new bundles
   in Vaultwarden. Verify new-identity-only decryption of a synthetic encrypted
   fixture in a controlled recovery test before touching repository ciphertext.
2. While old keys still decrypt the current tree, prepare the distributed key bundle
   containing **old plus new** identities, encrypted for currently usable recipients.
   Explicitly verify the encrypted `sops-age` resource itself is recoverable. Deliver
   the overlap bundle to every consumer and verify rollout/readiness before changing
   any file to require a new identity. No old key is removed in this phase.
3. Update creation rules and recipients with an overlap set, run `updatekeys` on the
   inventoried files, validate, and confirm reconciliation across **all** namespaces.
   Test new-key-only decryption of approved representative files, including the key
   bundle, without disclosing plaintext. Do not rely on a test that also loaded old keys.
4. Only once every consumer has the new identities, remove old recipients from the
   creation rules, update file recipients, and rotate data keys for the inventoried
   files. Validate again, including new-key-only recovery of the distributed key
   bundle, and observe successful reconciliation everywhere before retiring any old
   private identity from active bundles. Keep approved historical recovery material
   securely archived, with its exposure risk understood.
5. Check Talos rendering/recovery separately; Flux does not apply machine configs.
   Any live mutation, decryption, encrypted-file edit, or push is separately approved.

Before retiring old identities, rollback is an approved Git revert to the last
known-good encrypted tree while retaining the overlap bundle in all consumers.
After retirement, reverting to old-recipient-only ciphertext may lock consumers out;
restore overlap access securely first, or re-encrypt the intended rollback with
current recipients. In a compromise, re-enabling a stolen key is not a safe rollback.

A known local decrypted artifact is **not inspected or removed by these checks**.
Any future cleanup of that artifact is explicitly deferred to a user-approved action.

References: [Flux SOPS](https://fluxcd.io/flux/guides/mozilla-sops/),
[Flux decryption](https://fluxcd.io/flux/components/kustomize/kustomizations/#decryption),
[SOPS key management](https://getsops.io/docs/).
