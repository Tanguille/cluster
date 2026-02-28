# Common Operations

**When to use:** add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, volsync, snapshot.

Step-by-step procedures for frequent cluster tasks.

## Adding a new application

Use **add-app-to-cluster** skill for full procedure.

1. Create namespace in `kubernetes/components/common/`
2. Add OCIRepository if external
3. Create app in `kubernetes/apps/<app>/`
4. Add Kustomization in appropriate `ks.yaml`
5. Run validation: `kubeconform -strict kubernetes/`

## Secrets management (SOPS)

1. Create unencrypted file first
2. Encrypt with: `sops --encrypt --in-place <file>`
3. Or create with: `sops <file>.yaml` (edits encrypted)

Never commit plaintext secrets or the age key. Use placeholders so I can add the secrets manually.

## Debugging

Use **debug-cluster** skill for structured 5-Whys analysis and troubleshooting.

## Backup & Restore

Use **backup-restore** skill for VolSync Kopia operations.
