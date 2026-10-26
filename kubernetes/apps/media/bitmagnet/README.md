# Bitmagnet stack

Bitmagnet (DHT crawler, `bitmagnet:3333`) and the Armiger connector
(`armiger-connector:8096`, Torznab proxy `:8097` for Prowlarr) share one
app-template HelmRelease. The database `bitmagnet` lives on the shared CNPG
cluster `postgres16`, reached through `pgbouncer-session`. The UI is on
`envoy-internal` only and unauthenticated.

## Operator notes

- The password lives in two SOPS secrets, `database/bitmagnet-db` (CNPG role)
  and `media/bitmagnet-postgres` (app). Rotate both together.
- DHT is outbound-only: `media` enforces PSA baseline, which forbids
  `hostPort`. Inbound DHT (faster crawl) would need a LoadBalancer IP plus
  router forwarding, like satisfactory.
- The `armiger-connector-data` PVC (venv + model) has no backup; it is
  re-downloadable. Do not delete it or the shared database on rollback.
- The init container pip-installs into the PVC at runtime. Direct dependencies,
  torch and the connector/model commits are pinned and tracked by Renovate;
  transitive ones are not. Connector and model bumps never automerge: the HF
  bundle contains unaudited executable Python.
- Each part reinstalls itself when its pin changes. Python minor bumps wait for
  dashboard approval until onnxruntime ships wheels for them.
- `config/classifier.yaml` deletes non-video content types after
  classification; unknown ones are kept. Apply it to existing rows with
  `bitmagnet reprocess --classifyMode rematch --contentTypes <types>`.
- `ARMIGER_VARIANT` stays ultra: on 500 local torrents int8 matched 496/500
  categories at the same speed and only 27 MiB less memory.
- `prune` (daily) deletes and blocklists TMDB-matched dubs with no English, Dutch
  or original-language track, and DHT-only swarms still at 0 seeders after 3 days.
- `config/connector.py` runs the pinned connector with a 1 h per-infohash prediction
  cache, so repeated Torznab results skip the model.
