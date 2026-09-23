# Laya triage server: a ~400M encoder model answering typed classification
# questions (choice / score / yes-no) over arbitrary text in one forward pass
# on CPU. The model weights are baked in at build time so the pod runs fully
# offline - no HF Hub access at runtime, no GPU, no persistence.
# See docs/llm-hosting/laya-triage.md.
FROM python:3.11-slim

ARG LAYA_VERSION=0.3.4
ARG LAYA_MODEL=convaiinnovations/laya

# CPU-only torch: this workload is a small encoder, and a CUDA stack would
# add ~2.5 GB of libraries to the image without serving a node we schedule
# this on.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir fastapi "uvicorn[standard]" huggingface_hub "laya==${LAYA_VERSION}"

# Bake the model in (public repo, no token needed). local_dir= keeps it flat;
# at runtime LAYA_MODEL_DIR points here and laya loads the local path
# directly, so no HF lookup ever happens.
RUN mkdir -p /model && \
    python -c "from huggingface_hub import snapshot_download; snapshot_download('${LAYA_MODEL}', local_dir='/model', allow_patterns=['rl_agent_config.json', 'model.safetensors', 'tokenizer/*', 'encoder/*'])" && \
    rm -rf /root/.cache

ENV LAYA_MODEL_DIR=/model \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRIES=1 \
    PYTHONUNBUFFERED=1

RUN groupadd -g 1000 laya && \
    useradd -u 1000 -g laya -d /app -m laya && \
    chown -R laya:laya /model

COPY --chown=laya:laya server.py /app/server.py
WORKDIR /app

USER laya
EXPOSE 8080

# The model load takes ~40 s on cluster CPUs; start-period gives it room
# before the k8s startup probe's own budget applies.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3); sys.exit(0 if r.status == 200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
