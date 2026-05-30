# CloudNativePG 1.29+ — image catalogs and extensions

This repo’s `Cluster` (`postgres16`) today uses a **pinned `imageName`** plus **`spec.postgresql.extensions`** with explicit **`image.reference`** entries (TensorChord images for vchord/pgvector). That is still valid.

[CloudNativePG 1.29](https://cloudnative-pg.io/docs/1.29/release_notes/v1.29/) adds **clearer, more centralized ways** to ship extensions:

## 1. Extensions inside an **ImageCatalog** (recommended upstream pattern)

- **`ImageCatalog`** / **`ClusterImageCatalog`** can list the **operand** image **and** **`extensions`** (extension OCI images) per PostgreSQL major.
- The **`Cluster`** then uses **`imageCatalogRef`** + **`major`** instead of a long-lived **`imageName`** pin; catalog updates (digests) drive **rolling updates** in one place.
- Catalogs with an **`extensions`** block require **operator ≥ 1.29** (older operators reject those definitions).

Docs: [Image catalog](https://cloudnative-pg.io/docs/1.29/image_catalog/), [Image volume extensions](https://cloudnative-pg.io/docs/1.29/imagevolume_extensions/).

Official manifests live in **`cloudnative-pg/artifacts`** ([image-catalogs](https://github.com/cloudnative-pg/artifacts/tree/main/image-catalogs), [image-catalogs-extensions](https://github.com/cloudnative-pg/artifacts/tree/main/image-catalogs-extensions)).

## 2. **Image volume** extension mounts

- Extensions are mounted as **read-only** volumes (Kubernetes **ImageVolume** where supported: containerd ≥ 2.1.0 or CRI-O ≥ 1.31).
- CNPG sets **`extension_control_path`** / **`dynamic_library_path`** from the mount layout; **1.29** adds **`bin_path`** and **`env`** on extension entries when binaries or env need to be set.

## 3. **Declarative `Database` resources** and `CREATE EXTENSION`

- For extensions that use **`CREATE EXTENSION`**, a **`Database`** CR can declare **`extensions`** so the operator runs **`CREATE EXTENSION IF NOT EXISTS`** for you.

Docs: [Declarative database management — extensions](https://cloudnative-pg.io/docs/1.29/declarative_database_management#managing-extensions-in-a-database).

## What **does not** move into an image catalog

**`pg_stat_statements`** is **not** a separate extension container in the CNPG sense: it ships **with the PostgreSQL build** (contrib) and must still be:

1. Listed in **`shared_preload_libraries`**, and  
2. Activated with **`CREATE EXTENSION pg_stat_statements`** per database (or via a **`Database`** CR as above).

The docs note that **modules loaded only via `shared_preload_libraries`** still need that GUC in the **`Cluster`**, even when other extensions come from catalogs.

## Adopting catalogs in *this* repo (optional)

- **Upgrade operator** to a **1.29.x** chart (see `kubernetes/apps/database/cloudnative-pg/app/ocirepository.yaml`) before relying on catalog-embedded extensions.
- **Custom** extension images (e.g. vchord) are **not** in the stock community catalog; you either maintain a **custom `ImageCatalog`** that references your operand + vchord images, or keep **direct `image.reference`** overrides on the `Cluster` (supported: catalog defaults can be overridden per cluster).
- Migrating from **`imageName`** → **`imageCatalogRef`** is a **planned** change (recovery, bootstrap, and image digests must stay consistent).

For day-to-day tuning context, see also `postgres-mcp-stats-and-pg-stat-statements.md`.
