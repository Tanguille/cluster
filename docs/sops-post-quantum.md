# SOPS post-quantum (age PQC)

This repo's Flux-managed secrets are encrypted with age post-quantum hybrid recipients (`age1pq1...`, X25519+ML-KEM-768), migrated from classical-only X25519 on 2026-07-11 via a zero-downtime dual-recipient rollover. Live in production; classical-only decrypt no longer works.

## Requirements

- **age** 1.3+ (e.g. via mise in `.mise.toml`)
- **SOPS** 3.12+ (getsops/sops with `filippo.io/age v1.3.1`)
- **Flux** release that bundles sops/age 1.3.x for decryption (kustomize-controller)

## What we learned

- **PQC support stack:** age v1.3.0+ adds post-quantum (hybrid ML-KEM-768 + X25519); recipients are `age1pq1...`, keypair via `age-keygen -pq`. SOPS (getsops/sops) and Flux kustomize-controller both depend on `filippo.io/age v1.3.1`, so encrypt (local/CI) and in-cluster decrypt work with PQC.
- **Why the first test failed:** SOPS creation rules in `.sops.yaml` are matched by **file path** only. Using stdin or `/dev/stdin` gives no path, so SOPS reports "no matching creation rules found". The long PQC public key also caused repeated "Public key:" output when piped.
- **Working test:** Use a real path that matches a rule, or an isolated temp dir with its own `.sops.yaml` (e.g. `path_regex: .*\.sops\.yaml` and `age: "<pq-public-key>"`). Then create a minimal secret file, `sops --encrypt --in-place`, and `SOPS_AGE_KEY_FILE=/path/to/pq.key sops --decrypt <file>`; success = decrypted YAML printed.
- **Migration touchpoints:** `.sops.yaml` holds the age public key(s) per path_regex; all `*.sops.yaml` files are encrypted to those recipients; the `sops-age` secret (e.g. under `kubernetes/components/common/`) holds the private key for Flux's kustomize-controller to decrypt. See `## Migration record` below for the actual dual-recipient rollover procedure used.

## Quick test (temp dir, no repo changes)

`.sops.yaml` only applies to paths under `talos/`, `bootstrap/`, or `kubernetes/`. To verify PQC without touching repo config, use a temp dir with its own `.sops.yaml`:

```bash
# 1. Generate PQC keypair
age-keygen -pq -o /tmp/pq-test.key
PQ_PUB=$(grep '^# public key:' /tmp/pq-test.key | cut -d: -f2 | tr -d ' ')

# 2. Test in a temp dir with its own .sops.yaml (so path matches)
mkdir -p /tmp/sops-pq-test && cd /tmp/sops-pq-test
cat > .sops.yaml << EOF
creation_rules:
  - path_regex: .*\.sops\.yaml
    age: "$PQ_PUB"
EOF

# 3. Minimal secret file and encrypt
echo -e 'apiVersion: v1\nkind: Secret\nmetadata: {name: pq-test}\nstringData: {foo: bar}' > test.sops.yaml
sops --encrypt --in-place test.sops.yaml

# 4. Decrypt (should print the secret)
SOPS_AGE_KEY_FILE=/tmp/pq-test.key sops --decrypt test.sops.yaml

# 5. Cleanup
rm -rf /tmp/sops-pq-test /tmp/pq-test.key
```

If step 4 prints the Secret YAML, SOPS + age support PQC.

## Migration record (2026-07-11, completed)

Verified live at every step before touching the next:

1. **Preflight:** confirmed live `kustomize-controller` was `v1.9.1`+ (well above the SOPS 3.12+/age 1.3+ floor) before starting.
2. **Generate PQ keypair**, back up the classical key, build a dual-identity `age.key` (`cat classical.key pq.key > age.key` — `age`/SOPS_AGE_KEY_FILE accepts multiple concatenated identity blocks).
3. **Dual-recipient `.sops.yaml`:** added the PQ recipient alongside classical in the `(bootstrap|kubernetes)/...` creation rule (comma-separated — SOPS wraps the data key to *every* listed recipient, so either identity alone decrypts).
4. **Re-encrypted `sops-age.sops.yaml` first, in isolation** — the highest-blast-radius file, since its decrypted content becomes the `sops-age` Secret Flux uses to decrypt everything else. Updated its `age.agekey` field to the dual-identity content (`sops set` requires the value to be JSON-encoded even for multi-line content — use `jq -Rs . age.key | sops set --value-stdin ...`, not `--value-file` with raw content) and ran `sops updatekeys -y` to rewrap its own outer encryption to dual-recipient. Verified both classical-only and PQ-only decrypt independently before merging. Once merged, confirmed live: `kustomize-controller` picked up the updated secret on its normal reconcile — **no controller restart needed**, since it re-reads the secretRef fresh every reconcile.
5. **Bulk re-encrypted the remaining ~42 secrets** to dual-recipient via `find ... | xargs -P4 sops updatekeys -y`. One file (`victoria-metrics/app/secret.sops.yaml`) is a multi-document YAML (two `Secret` objects, `---`-separated) — SOPS encrypts each document independently, so recipient-count verification needs to account for document count, not assume a flat 1-per-file.
6. **Burn-in:** ~2 hours (a few Flux reconcile cycles) — sufficient because `sops updatekeys` only rewraps recipients and never touches secret plaintext (Flux logged `"unchanged"` for every resource throughout). Confirmed zero decrypt errors across multiple independent reconcile cycles before cutting over.
7. **Cutover:** dropped the classical recipient from `.sops.yaml`, re-wrapped all in-scope files (including `sops-age.sops.yaml`'s outer encryption) to PQ-only. The `sops-age` secret's *plaintext* content still holds both the classical and PQ private keys — only the encryption target changed, so the classical key remains a rollback path even though nothing is encrypted to it anymore. Verified live: classical-only decrypt now correctly fails, PQ-only decrypts everything, Flux reconciled cleanly with zero decrypt errors.
8. **`talhelper`/`talos` follow-up:** `talos/talsecret.sops.yaml` was out of scope for this migration (decrypted by `talhelper`/`talosctl`, not Flux). Checked `talhelper` v3.1.12's vendored deps: `github.com/getsops/sops/v3 v3.13.1` + `filippo.io/age v1.3.1`, both above the PQ floor — **it's compatible**, so talos secrets can be migrated the same way in a follow-up, not a permanent classical-only exclusion.

**Operational notes for next time:**
- `age.key` is now dual-identity and the sole local copy of both private keys outside the cluster — keep a durable offline backup of it.
- Multi-recipient decrypt is per-recipient-independent by design (verified against `kustomize-controller`/SOPS source) — worth re-confirming for any similarly self-referential secret-store change.
