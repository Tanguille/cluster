FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS source
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY . /build
RUN python /build/prepare.py
RUN python -m venv /venv && /venv/bin/pip install --require-hashes -r /build/requirements.lock \
    && /venv/bin/pip install --no-build-isolation --no-deps /src

FROM source AS test
RUN /venv/bin/pip install --require-hashes -r /build/test-requirements.lock
WORKDIR /src
RUN /venv/bin/python -m pytest -q tests /build/tests

FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS runtime
ARG REVISION=local-unpublished
LABEL org.opencontainers.image.source="https://github.com/Tanguille/cluster" \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.version="0.4.2-monitor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.upstream.revision="e8e24749470c5d3e6419e5322f698eeee920396d"
COPY --from=source /venv /venv
COPY --chmod=0444 MONITOR_ONLY /venv/lib/python3.13/site-packages/opnsense_mcp/MONITOR_ONLY
COPY --from=source /src/LICENSE /usr/share/licenses/opnsense-mcp/LICENSE
COPY upstream.lock.json requirements.lock /usr/share/opnsense-mcp/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    OPNSENSE_MONITOR_ONLY=true OPNSENSE_API_STYLE=snake_case
USER 65532:65532
ENTRYPOINT ["/venv/bin/opnsense-mcp"]
