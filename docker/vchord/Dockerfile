# VectorChord as a CloudNativePG ImageVolume extension image, from the pgsty/vchord fork's release deb.
# The official ghcr.io/tensorchord/vchord-scratch has no PostgreSQL 19 tag; the fork publishes
# debs for PG15-19 but no image. Installing the deb here also gives CNPG's lib/ share/ layout,
# which vchord-scratch lacks.

ARG PG_MAJOR=19
FROM ghcr.io/cloudnative-pg/postgresql:${PG_MAJOR}-minimal-trixie AS builder

ARG PG_MAJOR
# renovate: datasource=github-releases depName=pgsty/vchord
ARG VCHORD_VERSION=1.5.0

USER 0

# The release publishes a .sha256 next to each deb; it catches a corrupt download, not a swapped release.
ADD https://github.com/pgsty/vchord/releases/download/${VCHORD_VERSION}/postgresql-${PG_MAJOR}-vchord_${VCHORD_VERSION}-1_amd64.deb /tmp/
ADD https://github.com/pgsty/vchord/releases/download/${VCHORD_VERSION}/postgresql-${PG_MAJOR}-vchord_${VCHORD_VERSION}-1_amd64.deb.sha256 /tmp/
RUN cd /tmp && sha256sum -c ./*.sha256 && dpkg-deb -x ./*.deb /out

FROM scratch
ARG PG_MAJOR

LABEL org.opencontainers.image.source="https://github.com/Tanguille/cluster" \
      org.opencontainers.image.description="VectorChord extension image for CloudNativePG" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# Licenses
COPY --from=builder /out/usr/share/doc/postgresql-${PG_MAJOR}-vchord/copyright /licenses/vchord/

# Libraries
COPY --from=builder /out/usr/lib/postgresql/${PG_MAJOR}/lib/vchord.so /lib/

# Share: control file, install script and every upgrade script
COPY --from=builder /out/usr/share/postgresql/${PG_MAJOR}/extension/vchord* /share/extension/

USER 65532:65532
