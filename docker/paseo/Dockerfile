# Paseo daemon with four preinstalled agents (OpenCode, Claude Code, Codex, Hermes ACP).
#
# The base is the upstream 0.13.0 image pinned by its multi-arch index digest
# (linux/amd64 manifest sha256:1e79ab931b54cf226f9dcae89825ba90030696e47db05a77e4965ae6b1c963bf).
# Only linux/amd64 is built: the gh release archive below is architecture-specific.
#
# Renovate manages the ARG pins. Every download is checksum-verified at build
# time; nothing is installed from a floating tag, `latest`, or a curl-to-shell
# installer.
FROM ghcr.io/getpaseo/paseo:0.13.0@sha256:9832cb6bcc904bb7fd6c0b22f1880cde38b592a26ecd38ba66c7fcc76fa0d43e

# renovate: datasource=github-releases depName=cli/cli
ARG GH_VERSION=2.103.2
# sha256 of gh_2.103.2_linux_amd64.tar.gz from the release's checksums file.
ARG GH_SHA256=62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8
# renovate: datasource=github-tags depName=NousResearch/hermes-agent
ARG HERMES_COMMIT=bf0203a794a07aac3ff764972ab9087baa4e4f4e
# sha256 of the codeload archive for HERMES_COMMIT (tag v0.22.5).
ARG HERMES_ARCHIVE_SHA256=0dc2cca54657d162845a77f634e107c61c3fb0bd6c8505212aa2e200591b7cc9

USER root
SHELL ["/bin/bash", "-c"]

# Hermes state lives on the mounted home. Paseo's own defaults (HOME, PASEO_HOME,
# PASEO_LISTEN, ...) are inherited from the base image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_HOME=/home/paseo/.hermes \
    PASEO_RELAY_ENABLED=false

# Debian bookworm's python3 (3.11) only runs Hermes' stdlib-only PM provisioner;
# the ACP runtime is the PM-locked CPython 3.14 below. gcc is kept for the rare
# dependency in the frozen lock without a cp314 wheel; Paseo's Node is untouched.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        ca-certificates curl git xz-utils python3 gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI: official release archive, verified against the published checksum.
RUN curl -fsSL --retry 3 -o /tmp/gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    && printf '%s  %s\n' "${GH_SHA256}" /tmp/gh.tar.gz | sha256sum -c - \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64"

# Agent CLIs come from the committed npm lock, installed under /usr/local (never
# HOME). `npm ci` fails if the lock and package.json disagree.
COPY package.json package-lock.json /opt/paseo-npm/
# npm installs the musl variants of the optional platform packages even on this
# glibc base. They can never execute here, so drop them (~0.65 GB).
RUN cd /opt/paseo-npm \
    && npm ci --omit=dev --no-audit --no-fund \
    && rm -rf node_modules/opencode-linux-x64-musl \
        node_modules/opencode-linux-x64-baseline-musl \
        node_modules/@anthropic-ai/claude-code-linux-x64-musl \
    && for command in opencode claude codex; do \
        ln -sfn "/opt/paseo-npm/node_modules/.bin/${command}" "/usr/local/bin/${command}"; \
    done

# Hermes source at the pinned commit, verified against the archive checksum.
RUN curl -fsSL --retry 3 -o /tmp/hermes.tar.gz \
        "https://codeload.github.com/NousResearch/hermes-agent/tar.gz/${HERMES_COMMIT}" \
    && printf '%s  %s\n' "${HERMES_ARCHIVE_SHA256}" /tmp/hermes.tar.gz | sha256sum -c - \
    && mkdir -p /opt/hermes \
    && tar -xzf /tmp/hermes.tar.gz -C /opt/hermes --strip-components=1 \
    && rm /tmp/hermes.tar.gz

WORKDIR /opt/hermes
# Hermes' own PM lock is the single authority for uv and CPython 3.14. PM's
# tarfile usage needs Python >= 3.12, which Debian bookworm's 3.11 is not, so
# the interpreter PM would install is bootstrapped from the same lock entry
# (URL + sha256 read out of pm/lock.json, never retyped) and used to run PM
# itself. Its CPython bundles SQLite 3.53.1, above the 3.51.3 WAL-reset
# threshold; Paseo's Debian 12 SQLite is never used by the ACP runtime.
ENV HERMES_RUNTIME_DIR=/opt/hermes/tools
RUN set -eu; \
    artifact="$(python3 -c 'import json; a = json.load(open("pm/lock.json"))["packages"]["python"]["artifacts"]["linux-x64"]; print(a["url"]); print(a["sha256"])')"; \
    url="$(sed -n 1p <<<"${artifact}")"; \
    sha="$(sed -n 2p <<<"${artifact}")"; \
    curl -fsSL --retry 3 -o /tmp/pm-python.tar.gz "${url}"; \
    printf '%s  %s\n' "${sha}" /tmp/pm-python.tar.gz | sha256sum -c -; \
    mkdir -p /opt/pm-bootstrap; \
    tar -xzf /tmp/pm-python.tar.gz -C /opt/pm-bootstrap; \
    rm /tmp/pm-python.tar.gz; \
    /opt/pm-bootstrap/python/bin/python3 --version

RUN set -eu; \
    /opt/pm-bootstrap/python/bin/python3 -c 'from pm import ensure; [ensure(name, explicit=True) for name in ("uv", "python", "npm", "ffmpeg", "ripgrep")]'; \
    /opt/pm-bootstrap/python/bin/python3 -c 'from pathlib import Path; from pm import installed_package; Path("/usr/local/bin/python3").symlink_to(installed_package("python").binary)'; \
    rm -rf /opt/pm-bootstrap; \
    python3 --version

# PM itself is resident, so no runtime install needs a compiler or /root.
RUN python3 -c 'from pathlib import Path; from pm import stage_manager_runtime; from scripts.bundles.payload import seal_pm_runtime; root = Path("/opt/hermes"); python = Path("/usr/local/bin/python3").resolve(); stage_manager_runtime(python=python, destination=root / "pm-runtime", project=root / "pm"); seal_pm_runtime(root, python)'

# Frozen, sealed ACP environment from uv.lock, using the PM-provisioned
# interpreter explicitly. Installed at build time: pod startup never downloads.
# --record-selection keeps this baseline when a later opt-in extra is installed.
RUN python3 -m pm.build_env --source /opt/hermes --python /usr/local/bin/python3 \
    --cache /opt/hermes/uv-cache --out /opt/hermes/.venv \
    --sealed --record-selection --extra acp

# The editable install's console script cannot import the pruned editable
# pointer in a sealed venv, so bind the source tree the same way upstream's
# docker/build_agent.py does and mint the launchers. /usr/local/bin/node stays
# Paseo's Node 22.
COPY assemble_runtime.py /tmp/assemble_runtime.py
RUN set -eu; \
    printf '{"schemaVersion":2,"commit":"%s","distribution":"docker","source":"github-archive","updateMechanism":"external","pmRuntime":"/opt/hermes/pm-runtime"}\n' "${HERMES_COMMIT}" \
        > /opt/hermes/install-stamp.json; \
    PYTHONPATH=/opt/hermes /opt/hermes/.venv/bin/python /tmp/assemble_runtime.py; \
    rm /tmp/assemble_runtime.py; \
    ln -sfn /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes; \
    rm -f /opt/hermes/.venv/.lock /opt/hermes/pm-runtime/.lock; \
    chmod 0644 /opt/hermes/tools/facts.json; \
    chmod -R a+rX /opt/hermes; \
    HOME=/tmp/hermes-check HERMES_HOME=/tmp/hermes-check/.hermes hermes --help >/dev/null; \
    rm -rf /tmp/hermes-check; \
    python3 -c 'import sqlite3, sys; v = sqlite3.sqlite_version_info; sys.exit("SQLite %s is older than 3.51.3" % sqlite3.sqlite_version) if v < (3, 51, 3) else None; print("sqlite", sqlite3.sqlite_version)'

# Default provider config and the fail-closed entrypoint wrapper. tini stays
# PID 1; the wrapper rejects an empty password before the upstream entrypoint
# performs its setup and execs the daemon.
COPY config.json /opt/paseo/config.json
COPY --chmod=0755 entrypoint.sh /usr/local/bin/paseo-entrypoint
COPY --chmod=0755 seed.sh /usr/local/bin/paseo-seed

RUN mkdir -p /home/paseo /workspace \
    && chown 1000:1000 /home/paseo /workspace \
    && chmod 0755 /home/paseo /workspace

WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paseo-entrypoint"]
# Pin the inherited command explicitly; the wrapper accepts only this daemon
# entrypoint after its password check. No base-image CMD can be reset silently.
CMD ["/usr/local/bin/paseo-docker-entrypoint"]
