# ghcr.io/tanguille/truenas-mcp — official iXsystems TrueNAS MCP server
# (github.com/truenas/truenas-mcp), repackaged as a container: upstream ships
# only static Go binaries in GitHub releases, no official image.
#
# The binary is a stdio MCP server (no HTTP listener), so the image is just
# the binary: the ToolHive operator launches it and speaks MCP over stdio.
# Read-only posture is enforced by the TrueNAS API key (a minimal, no-
# permission key) — the official binary has no --readonly flag, unlike the
# abandoned truenas-master-mcp this replaces.
#
# Release version (Renovate-managed: bump this on a truenas-mcp release).
# renovate: datasource=github-releases depName=truenas/truenas-mcp
ARG VERSION=v0.0.6

# buildx builds for linux/amd64 by default in CI, but the asset name must
# match the platform being built, not the host running the build.
FROM golang:1.25 AS fetch
ARG VERSION
# Verify against the release's own checksums.txt. The download MUST keep the
# exact asset name recorded in checksums.txt: sha256sum -c matches on the
# recorded filename, and a renamed tarball makes it "no file was verified"
# (exit 1) even with --ignore-missing.
RUN set -eux; \
    ver="${VERSION}"; \
    base="https://github.com/truenas/truenas-mcp/releases/download/${ver}"; \
    curl -fsSL --retry 3 -o /tmp/truenas-mcp-linux-amd64.tar.gz \
        "${base}/truenas-mcp-linux-amd64.tar.gz"; \
    curl -fsSL --retry 3 -o /tmp/checksums.txt \
        "${base}/checksums.txt"; \
    ( cd /tmp && sha256sum -c --ignore-missing checksums.txt ); \
    tar -xzf /tmp/truenas-mcp-linux-amd64.tar.gz -C /tmp

# debian:bookworm-slim: small glibc base so the dynamically-linked Go binary
# runs with no further deps (ldd: libc.so.6 only). No shell, no user, no
# workdir — the entrypoint IS the MCP server.
FROM debian:bookworm-slim
COPY --from=fetch /tmp/truenas-mcp-linux-amd64 /usr/local/bin/truenas-mcp
# Default flags; env vars TRUENAS_URL / TRUENAS_API_KEY (set by the MCPServer
# CRD) are picked up by the binary. No -insecure: the box is reached through
# its Let's Encrypt name (truenas.${SECRET_DOMAIN}) and TLS stays verified.
ENTRYPOINT ["truenas-mcp"]
