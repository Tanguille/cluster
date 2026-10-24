# Bitmagnet stack

Bitmagnet (DHT crawler, `bitmagnet:3333`) and the Armiger connector
(`armiger-connector:8096`, Torznab proxy `:8097` for Prowlarr) share one
app-template HelmRelease. The database `bitmagnet` lives on the shared CNPG
cluster `postgres16`. The UI is on `envoy-internal` only and unauthenticated.

## Operator notes

- The password lives in two SOPS secrets, `database/bitmagnet-db` (CNPG role)
  and `media/bitmagnet-postgres` (app). Rotate both together.
- DHT uses `hostPort` 3334 TCP/UDP on the scheduled node. Any router NAT
  forwarding must follow that node; nothing here configures it.
- The `armiger-connector-data` PVC (venv + model) has no backup; it is
  re-downloadable. Do not delete it or the shared database on rollback.
- The init container pip-installs into the PVC at runtime. Direct dependencies
  are pinned, transitive ones are not. The connector source is SHA-256 verified;
  the pinned HF bundle contains unaudited executable Python.
- Changing requirements or the image ABI: bump the `runtime-v*` suffix of
  `ARMIGER_RUNTIME_DIR` in the HelmRelease so a stale `.ready` marker cannot
  suppress the reinstall.
