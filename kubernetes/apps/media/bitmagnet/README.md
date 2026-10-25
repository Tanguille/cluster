# Bitmagnet stack

Bitmagnet (DHT crawler, `bitmagnet:3333`) and the Armiger connector
(`armiger-connector:8096`, Torznab proxy `:8097` for Prowlarr) share one
app-template HelmRelease. The database `bitmagnet` lives on the shared CNPG
cluster `postgres16`. The UI is on `envoy-internal` only and unauthenticated.

## Operator notes

- The password lives in two SOPS secrets, `database/bitmagnet-db` (CNPG role)
  and `media/bitmagnet-postgres` (app). Rotate both together.
- DHT is outbound-only: `media` enforces PSA baseline, which forbids
  `hostPort`. Inbound DHT (faster crawl) would need a LoadBalancer IP plus
  router forwarding, like satisfactory.
- The `armiger-connector-data` PVC (venv + model) has no backup; it is
  re-downloadable. Do not delete it or the shared database on rollback.
- The init container pip-installs into the PVC at runtime. Direct dependencies
  are pinned, transitive ones are not. The connector source is SHA-256 verified;
  the pinned HF bundle contains unaudited executable Python.
- The venv reinstalls itself when the python minor or a pin changes; Renovate
  holds python minor bumps for dashboard approval until onnxruntime ships wheels.
- `classifier.yml` deletes non-video content types after classification;
  unknown ones are kept. Existing rows are not affected until reprocessed.
