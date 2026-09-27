# CLM projection-head server (the "System-1" classifier) for CPU alert
# triage. The head is 20M params (state+action projections, InfoNCE); the
# 8B encoder is NOT here — it runs as an LLMKube llama.cpp embedding
# service (kubernetes/apps/ai/llmkube/models/clm-encoder.yaml) and is
# reached over the in-cluster network (CLM_EMB_URL).
#
# The checkpoint is baked in at build time (pinned by git sha below) so the
# pod runs fully offline: no HF Hub access at runtime.
# See docs/llm-hosting/clm-triage.md.
FROM python:3.11-slim

ARG CLM_VERSION=0.1.0
# Pinned to the CLM-v0.1-8B repo commit (immutable bytes); Renovate bumps it
# via the huggingface hub manager like the other model shas.
ARG CLM_CKPT_SHA=e939398d4556fcd9400c76fa8c5a513202f42b0a

# CPU-only torch: the head runs on CPU by design. The upstream package also
# depends on vllm (for the GPU encoder path we replace with llama.cpp), so
# install it --no-deps and pull only what the serve path actually imports.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir "contrastive-lm==${CLM_VERSION}" --no-deps && \
    pip install --no-cache-dir numpy requests fastapi "uvicorn[standard]"

# Bake the head in (~75 MB).
RUN mkdir -p /head && \
    python -c "import urllib.request; urllib.request.urlretrieve('https://huggingface.co/Contrastive-LM/CLM-v0.1-8B/resolve/${CLM_CKPT_SHA}/CLM_v0.1-8B.pt', '/head/CLM_v0.1-8B.pt')" && \
    rm -rf /root/.cache

ENV CLM_CKPT=/head/CLM_v0.1-8B.pt \
    CLM_DEVICE=cpu \
    CLM_PORT=8080 \
    PYTHONUNBUFFERED=1

RUN groupadd -g 1000 clm && \
    useradd -u 1000 -g clm -d /app -m clm && \
    chown clm:clm /head

USER clm
EXPOSE 8080

# Head load is 1-2 s; 60 s of start-period leaves room for a slow node.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3); sys.exit(0 if r.status == 200 else 1)"

CMD ["clm-serve", "--device", "cpu", "--no-ui", "--no-download"]
