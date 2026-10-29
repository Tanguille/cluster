# Bitmagnet stack

Bitmagnet is a DHT crawler; Prowlarr queries its Torznab API at
`bitmagnet:3333/torznab`. The database `bitmagnet` lives on the shared CNPG
cluster `postgres16`, reached through `pgbouncer-session`. The UI is on
`envoy-internal` only and unauthenticated.

## Operator notes

- The password lives in two SOPS secrets, `database/bitmagnet-db` (CNPG role)
  and `media/bitmagnet-postgres` (app). Rotate both together.
- DHT is outbound-only: `media` enforces PSA baseline, which forbids
  `hostPort`. Inbound DHT (faster crawl) would need a LoadBalancer IP plus
  router forwarding, like satisfactory.
- `config/classifier.yaml` deletes non-video and unknown content types after
  classification, anything below 720p or from cam/DVD sources, and malware
  lures (any executable or script file except RARBG's marker). CEL string
  literals reject `\.`; use `[.]` in regexes. Apply it to existing rows with
  `bitmagnet reprocess --classifyMode rematch --contentTypes <types>`.
- The unused trigram index `torrent_tags_name_idx1` (migration 00003, 261 MB)
  was dropped by hand. Tag lookups use the btree `torrent_tags_name_idx`.
- `prune` (daily) deletes and blocklists TMDB-matched dubs with no English, Dutch
  or original-language track, and DHT-only swarms still at 0 seeders after 3 days.
  Its scripts live in the `bitmagnet-prune` ConfigMap, so editing them does not
  restart bitmagnet.
- bitmagnet always advertises imdbid/tmdbid but answers id searches only from
  TMDB-matched torrents. Prowlarr indexer 130 refetches caps on every save, so
  they cannot be trimmed there.
- qBittorrent's excluded file names (UI setting) skip executables in every
  download, and Sonarr/Radarr fail imports that contain one.
