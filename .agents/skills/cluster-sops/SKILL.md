---
name: cluster-sops
description: >-
  Decrypt, create, edit, and re-encrypt SOPS secrets for this repo — on the remote
  management host, where the age key lives, using a single scripted round-trip.

  user: "add a new secret for app X" → remote sops encrypt with the right recipients
  user: "change the DB password" → decrypt → edit in dict form → re-encrypt
  user: "re-encrypt after changing recipients" → sops updatekeys

  Use proactively whenever a *.sops.yaml value must be created or changed. Ask the
  owner first — decrypting/editing SOPS is an ask-first operation per AGENTS.md.
compatibility: Requires `ssh` to `tanguille@192.168.0.181` (key at /opt/data/.ssh/id_ed25519), `sops` + `python3` + `mise` on that host, and the repo's `.sops.yaml` + `age.key` under `~/cluster/`.

---

# Cluster SOPS (remote secrets workflow)

## Where it runs — and why

- The **agent box has no age key** (no `age.key` under the config root) and no usable
  local `sops` config for the right recipients. Every decrypt/encrypt/re-encrypt
  **must run on the management host**: `tanguille@192.168.0.181`, repo at `~/cluster`,
  key at `~/cluster/age.key`.
- **SSH from the agent box** (verified working form):
  `ssh -i /opt/data/.ssh/id_ed25519 tanguille@192.168.0.181 '<cmd>'`
  - The `k8s-management` alias in `~/.ssh/config` is **dead in this environment**:
    the passwd home is `/opt/data` but `$HOME=/opt/data/home`, so OpenSSH reads
    `/opt/data/.ssh/config` (absent) and the alias never resolves. Don't use it until
    the config is placed at `/opt/data/.ssh/config` (or symlinked).
  - Use `BatchMode=yes` for scripted runs.
- **`sops` on the remote is a mise shim** — bare `sops --version` can print nothing.
  Run it as `mise exec -- sops …` (or from a `mise trust`-ed directory) so it resolves.

## Recipients (from `.sops.yaml` — read it, don't trust memory)

| path_regex | recipient |
|---|---|
| `talos/.*\.sops\.ya?ml` | `age12gul5m0…` (short) |
| `(bootstrap\|kubernetes)/.*\.sops\.ya?ml` | `age1pq1f69…` (post-quantum, long) |

`sops` picks the rule by path automatically — so **run it against the file's real
path** (or pass the file inside the matching subtree). Both halves of the age key
must be in `age.key`: a key missing the PQ half decrypts `talos/` but fails on
`kubernetes/`.

## The three traps (each already cost time — avoid them)

1. **`stringData` → `data` on decrypt/encrypt.** SOPS normalizes `stringData` into
   base64 `data` (and `encrypted_regex: ^(data|stringData)$` only encrypts those keys).
   A text round-trip (decrypt → edit → encrypt) can **drop keys or mangle the mapping**.
   **Fix:** rebuild with a Python dict on the remote — load decrypted YAML, set the
   values in the right section, dump back — instead of hand-editing text.
2. **Stale `sops:` footer.** Text-editing an already-encrypted file (or a decrypt
   that left the metadata block) makes the next `sops encrypt` fail on the existing
   footer. **Fix:** always start from a *fully decrypted* file (metadata stripped)
   before re-encrypting; never edit the ciphertext by hand.
3. **Missing `--input-type`/`--output-type`.** Without `--input-type yaml
   --output-type yaml`, sops may guess JSON and fail on YAML secrets. **Fix:** pass
   both flags explicitly every time.

## Canonical flows (remote, one scripted call each)

### New secret value

```bash
ssh -i /opt/data/.ssh/id_ed25519 tanguille@192.168.0.181 '
set -e; cd ~/cluster
# 1. stage the plaintext in the right subtree (path must match the .sops.yaml rule)
#    e.g. kubernetes/apps/<ns>/<app>/<name>.sops.yaml with data: {key: value}
# 2. encrypt in place:
mise exec -- sops encrypt --in-place \
  --input-type yaml --output-type yaml kubernetes/apps/<ns>/<app>/<name>.sops.yaml
# 3. sanity: only data/stringData encrypted, sops footer present, nothing plaintext
grep -n "ENC[A-Z0-9]\{32,\}" kubernetes/apps/<ns>/<app>/<name>.sops.yaml | head
'
```

### Change an existing value (dict-based, no text surgery)

```bash
ssh -i /opt/data/.ssh/id_ed25519 tanguille@192.168.0.181 '
set -e; cd ~/cluster; F=kubernetes/apps/<ns>/<app>/<name>.sops.yaml
mise exec -- sops decrypt --input-type yaml --output-type yaml "$F" > /tmp/<name>.plain.yaml
python3 - <<PY
import yaml
p = "/tmp/<name>.plain.yaml"
d = yaml.safe_load(open(p))
# set the new value where it belongs (data: is base64; stringData: is plain —
# normalize to the shape the repo uses for this file before writing)
d["data"]["key"] = "newbase64value"
yaml.safe_dump(d, open(p, "w"), sort_keys=False)
PY
mise exec -- sops encrypt --in-place --input-type yaml --output-type yaml "$F"
rm -f /tmp/<name>.plain.yaml   # ask-first: never leave decrypted temp behind
'
```

### Re-encrypt after a recipient change

```bash
mise exec -- sops updatekeys --in-place \
  --input-type yaml --output-type yaml <file>
```

### Add a CNPG managed role / DB secret (CNPG subtree)

Same pattern; the CNPG role secret lives under the cloudnative-pg app path (its
`managed.roles[].passwordSecret` points at it), so it encrypts under the
`kubernetes/` rule. The role itself is added in `cluster.yaml` (plaintext), the
password in the `.sops.yaml`.

## Standing rules

- **Ask before decrypting or editing any `*.sops.yaml`** (AGENTS.md), and before
  `rm`-ing a decrypted temp file.
- **Never echo decrypted values** into chat, PR descriptions, logs, or commit
  messages. Use `[REDACTED]`.
- The remote `~/cluster` checkout may sit on a **stale feature branch** (it was on
  `feat/truenas-mcp` during the kguardian work). SOPS only needs `.sops.yaml` +
  `age.key`, but `git fetch` + check the branch before trusting file state there.
- After encryption: `git diff` on the remote should show **only ciphertext changes**
  (no plaintext, no `stringData`/`data` shape drift beyond what SOPS itself did).
- Commit the `.sops.yaml` change in the agent-box worktree (pull the encrypted bytes
  back with `scp`), then follow the normal commit/PR flow — the remote is for
  SOPS only, not for `git push` (which needs a token; use bundles or ToolHive
  `push_files` per the PR-shepherding rules in `common-operations.md`).
