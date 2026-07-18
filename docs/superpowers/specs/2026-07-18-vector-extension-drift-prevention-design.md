# Vector extension drift prevention

## Context

The pinned CloudNativePG PostgreSQL 18 operand currently exposes `vector`
version `0.8.5`, while the inspected database has version `0.8.2` installed.
Replacing an extension image makes new extension files available but does not
run `ALTER EXTENSION ... UPDATE`. The existing `Database` resources only ensure
that `vector` and `vchord` are present, so they do not declare a desired
installed version.

The PostgreSQL operand and `vchord-scratch` image references are already
covered by the repository's pinned upstream Renovate preset
`home-operations/renovate-presets#2.1.0`. Duplicate local Renovate annotations
on CNPG extension references create malformed duplicate dependency extraction;
they are not a reliable update mechanism.

## Goals

- Reconcile the known `vector` installations to the version available in the
  pinned operand image.
- Require every declared CNPG `vector` or `vchord` extension to specify its
  intended installed version.
- Block a pull request that changes an extension-bearing image without also
  updating the matching extension target version.
- Simplify local Renovate configuration while retaining the upstream preset as
  the source of CNPG image parsing and versioning.

## Non-goals

- Automatically apply database changes outside Flux.
- Track pgvector's GitHub releases independently of the pinned PostgreSQL
  operand image; a release may not be packaged in that image yet.
- Migrate extension installations that are not represented by a `Database` CR.
  Post-deployment inventory will identify any such databases for a separate,
  explicit migration.

## Design

### Declarative extension versions

Set the target version for every existing `vector` and `vchord` entry in the
CNPG `Database` manifests. Use `vector: 0.8.5`, the version reported as
available by the pinned operand image, and use the matching vchord release
version extracted from the `vchord-scratch` image tag. CloudNativePG will
reconcile these declarations with `ALTER EXTENSION ... UPDATE TO` after Flux
applies them, provided the image includes the supported upgrade path.

### Version-compatibility guard

Add a deterministic CI validation that uses the exact pinned images:

1. Read every `Database` CR that declares `vector` or `vchord`; reject missing
   target versions.
2. Start or inspect the pinned PostgreSQL operand image and obtain its available
   `vector` version from PostgreSQL package metadata.
3. Require all declared `vector` targets to equal that available version.
4. Parse the pinned `vchord-scratch` tag and require all declared `vchord`
   targets to equal its extension version.

The guard runs on pull requests. A Renovate image update therefore cannot merge
until its corresponding declarative extension target is reviewed and updated in
the same change. It deliberately does not use the pgvector GitHub release as a
source of truth because that can be ahead of the package available in the
pinned operand image.

### Renovate cleanup

Retain `github>home-operations/renovate-presets#2.1.0` and remove local CNPG
annotations that duplicate its CNPG manager. Repair the proven broken Grafana
dashboard URL regex manager. Narrow the reviewed over-broad paths and package
matchers, and remove `minimumGroupSize` only from groups whose members are not
required to release in lockstep. Keep qBitrr's existing, proven manager
behavior unchanged in this PR.

## Rollout and validation

1. CI validates the manifest targets against the pinned images and renders the
   changed Kubernetes manifests.
2. Merge the PR; Flux performs the declarative extension update. No direct
   cluster mutation is used.
3. Confirm each affected `Database` resource is applied and query every
   database for `pg_extension.extversion` and
   `pg_available_extensions.default_version` for `vector` and `vchord`.
4. If inventory finds an extension outside a `Database` CR, add a separate
   adoption manifest before managing its version.

## Failure handling

If the desired version has no supported upgrade path, the `Database` resource
will not become applied. Do not bypass it with manual SQL: retain the current
version declaration, investigate the vendor upgrade path, and make the
recovery migration an explicit GitOps change.
