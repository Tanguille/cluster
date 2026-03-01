# SOPS post-quantum (age PQC)

This repo might use age post-quantum (recipients `age1pq1...`) with SOPS soon. Flux in-cluster decryption supports it when kustomize-controller is built with SOPS 3.12+ and age 1.3+.

## Requirements

- **age** 1.3+ (e.g. via mise in `.mise.toml`)
- **SOPS** 3.12+ (getsops/sops with `filippo.io/age v1.3.1`)
- **Flux** release that bundles sops/age 1.3.x for decryption (kustomize-controller)

## What we learned

- **PQC support stack:** age v1.3.0+ adds post-quantum (hybrid ML-KEM-768 + X25519); recipients are `age1pq1...`, keypair via `age-keygen -pq`. SOPS (getsops/sops) and Flux kustomize-controller both depend on `filippo.io/age v1.3.1`, so encrypt (local/CI) and in-cluster decrypt work with PQC.
- **Why the first test failed:** SOPS creation rules in `.sops.yaml` are matched by **file path** only. Using stdin or `/dev/stdin` gives no path, so SOPS reports "no matching creation rules found". The long PQC public key also caused repeated "Public key:" output when piped.
- **Working test:** Use a real path that matches a rule, or an isolated temp dir with its own `.sops.yaml` (e.g. `path_regex: .*\.sops\.yaml` and `age: "<pq-public-key>"`). Then create a minimal secret file, `sops --encrypt --in-place`, and `SOPS_AGE_KEY_FILE=/path/to/pq.key sops --decrypt <file>`; success = decrypted YAML printed.
- **Migration touchpoints:** `.sops.yaml` holds the age public key(s) per path_regex; all `*.sops.yaml` files are encrypted to those recipients; the `sops-age` secret (e.g. under `kubernetes/components/common/`) holds the private key for Flux's kustomize-controller to decrypt. Migrating to PQC = new PQC keypair, update `.sops.yaml`, re-encrypt all SOPS files, update `sops-age` with the new private key, and use a Flux release that bundles sops/age 1.3.x.

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

## Migration checklist

For a new environment or re-migration:

1. Generate PQC keypair: `age-keygen -pq`; keep the private key secure and the public key for `.sops.yaml`.
2. Update `.sops.yaml`: set `age` in the relevant creation rules to the new `age1pq1...` public key.
3. Re-encrypt all `*.sops.yaml` files (e.g. decrypt with old key, re-encrypt with new, or use both recipients during transition then drop the old).
4. Update the `sops-age` secret (e.g. in `kubernetes/components/common/`) with the new private key so Flux can decrypt.
5. Use a Flux release whose kustomize-controller bundles SOPS 3.12+ and age 1.3.x; run `task reconcile` or equivalent and confirm Kustomizations apply.
