FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ARG RUNTIME_REVISION=5bd7c69419487a783cbce18dfd41f707441a1b85
ADD --checksum=sha256:7077c732b173641b1acbddd6e310eecc537fc4d16678d8a7ab0f2c820bc28795 https://codeload.github.com/vllm-project/semantic-router/tar.gz/${RUNTIME_REVISION} /tmp/runtime.tar.gz

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/var/cache/vllm-srun

RUN mkdir -p /opt/vllm-srun "$HF_HOME" \
    && tar -xzf /tmp/runtime.tar.gz -C /opt/vllm-srun --strip-components=3 --wildcards '*/src/model-runtime/*' \
    && rm /tmp/runtime.tar.gz \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.10.0 \
    && pip install -c /opt/vllm-srun/requirements-lock.txt '/opt/vllm-srun[multimodal]' \
    && useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin runtime \
    && chown runtime "$HF_HOME"

USER 65532
EXPOSE 8100
ENTRYPOINT ["vllm-srun"]
LABEL org.opencontainers.image.source="https://github.com/Tanguille/cluster"
