# TimescaleDB community (TSL) build as a CloudNativePG ImageVolume extension image.
# Mirrors cloudnative-pg/postgres-extensions-containers timescaledb-oss, but installs
# Timescale's own package: the PGDG one is the Apache-2.0 edition without timescaledb-tsl.

ARG PG_MAJOR=18
FROM ghcr.io/cloudnative-pg/postgresql:${PG_MAJOR}-minimal-trixie AS builder

ARG PG_MAJOR
# renovate: datasource=github-releases depName=timescale/timescaledb
ARG TIMESCALEDB_VERSION=2.32.1

USER 0

# The checksum pins the repo signing key; a rotation fails the build instead of trusting a new key.
ADD --chmod=644 --checksum=sha256:eb53bd3dc31e06fbf1ed769139f12b1fffaf1958a96c9b4f9727c19aada83321 \
    https://packagecloud.io/timescale/timescaledb/gpgkey /etc/apt/keyrings/timescaledb.asc

# Package versions carry the PG minor they were built against (2.32.1~debian13-1807), so the
# glob takes the newest build; apt upgrades postgresql in this stage if that build needs it.
RUN echo "deb [signed-by=/etc/apt/keyrings/timescaledb.asc] https://packagecloud.io/timescale/timescaledb/debian/ trixie main" \
        > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        "timescaledb-2-postgresql-${PG_MAJOR}=${TIMESCALEDB_VERSION}~*" \
        "timescaledb-2-loader-postgresql-${PG_MAJOR}=${TIMESCALEDB_VERSION}~*" \
    && dpkg-query -W "timescaledb-2-*" "postgresql-${PG_MAJOR}"

# The deb ships every versioned library back to 2.23 (440 MB). Keep the current and two previous:
# the deployed version's .so must stay so CNPG's ALTER EXTENSION UPDATE can run, and the extra one
# covers a single skipped release.
RUN cd "/usr/lib/postgresql/${PG_MAJOR}/lib" \
    && keep="$(printf '%s\n' timescaledb-tsl-*.so | sed 's/^timescaledb-tsl-\(.*\)\.so$/\1/' | sort -V | grep -Fx -B2 "${TIMESCALEDB_VERSION}")" \
    && for f in timescaledb-*.so; do \
        v="${f##*-}"; \
        echo "${keep}" | grep -Fxq "${v%.so}" || rm "${f}"; \
    done \
    && ls -l timescaledb*

FROM scratch
ARG PG_MAJOR

LABEL org.opencontainers.image.source="https://github.com/Tanguille/cluster" \
      org.opencontainers.image.description="TimescaleDB community (TSL) extension image for CloudNativePG" \
      org.opencontainers.image.licenses="Apache-2.0 AND LicenseRef-Timescale"

# Licenses
COPY --from=builder /usr/share/doc/timescaledb-2-postgresql-${PG_MAJOR}/copyright /licenses/timescaledb-2-postgresql-${PG_MAJOR}/

# Libraries
COPY --from=builder /usr/lib/postgresql/${PG_MAJOR}/lib/timescaledb* /lib/

# Share
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/extension/timescaledb* /share/extension/

USER 65532:65532
