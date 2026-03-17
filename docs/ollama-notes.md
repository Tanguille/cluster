# Ollama (AI namespace)

References: [FAQ](https://docs.ollama.com/faq), [Context length](https://docs.ollama.com/context-length), [Troubleshooting](https://docs.ollama.com/troubleshooting), [GPU](https://docs.ollama.com/gpu).

## Env vars from docs

- **OLLAMA_NO_CLOUD=1** – [FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features): local-only, disables cloud models and web search.
- **OLLAMA_KV_CACHE_TYPE** – [FAQ](https://docs.ollama.com/faq#how-can-i-set-the-quantization-type-for-the-kv-cache): `q8_0` (default `f16`) uses ~half the K/V cache memory with minimal quality impact; useful with large context on limited VRAM.

## Context length

Set when serving: `OLLAMA_CONTEXT_LENGTH=64000 ollama serve` ([context-length](https://docs.ollama.com/context-length)). VRAM-based defaults otherwise: &lt; 24 GiB → 4k, 24–48 GiB → 32k, ≥ 48 GiB → 256k.

Per request: use `options.num_ctx` in `/api/chat` or `/api/generate` ([FAQ](https://docs.ollama.com/faq#how-can-i-specify-the-context-window-size)):

```json
{ "model": "llama3.2", "options": { "num_ctx": 65536 }, ... }
```

CLI: `/set parameter num_ctx 4096` in `ollama run`.

## Init error: `ERROR: init 250 result=11`

[Troubleshooting](https://docs.ollama.com/troubleshooting#nvidia-gpu-discovery) documents GPU init error codes (3, 46, 100, 999, “or others”). Result 11 is errno **EAGAIN**. Suggested steps:

- Latest NVIDIA drivers; reboot; `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm`; `sudo nvidia-modprobe -u`
- For more diagnostics: set `CUDA_ERROR_LEVEL=50` in the pod env and check logs
- Our Helm values: `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, init container delay
