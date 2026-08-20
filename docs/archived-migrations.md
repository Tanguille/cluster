# Archived Migrations

The `archive/` directory (1,904 lines, 5 files) was removed on 2026-08-20. Every procedure in it had already been completed, and the infrastructure each one targeted is gone, so the files read as current procedure while describing a cluster that no longer exists.

## What was there

| File | What it did | Why it is dead |
| --- | --- | --- |
| `migrate-pvs.sh` | pv-migrate driver, `openebs-zfs` → `ceph-block` | No ZFS storage class remains; the script also skips "volsync-backed PVCs" and volsync was decommissioned (see [kopiur-restore.md](kopiur-restore.md)) |
| `migrate-to-postgres.sh`, `MIGRATION_GUIDE.md` | Radarr SQLite → CloudNativePG cutover | Cutover completed; Radarr runs on CNPG |
| `opnsense-bgp-setup.md`, `check-opnsense-bgp.md` | BGP peering between OPNsense and the cluster | Never adopted; LoadBalancer IPs are static, assigned via Cilium L2 announcements (`cluster-settings.yaml`) |

## Recovering them

The last commit containing `archive/` is `21d8bbd03` (2026-08-02). Nothing is lost, git keeps the full history:

```sh
# read one file without checking anything out
git show 21d8bbd03:archive/migrate-pvs.sh

# restore the whole directory into the working tree
git checkout 21d8bbd03 -- archive/

# find the directory's full history if the sha above ever goes stale
git log --full-history --oneline -- archive/
```

If you restore one of these, treat it as a starting point and not a runbook: they were written against storage classes, chart versions, and a network topology the cluster has since moved off.
