# Hermes web extract rerank plugin

When `web_extract` truncates a page (Hermes's `[TRUNCATED]` footer), this plugin keeps the page start and
replaces the rest of the window with the passages most relevant to the task's last `web_search` query:
BM25 picks up to 12 candidates, then the in-cluster `bge-reranker-v2-m3` cross-encoder ranks them. Same
character budget, same footer (stored path and `read_file` hint). Standard library only.

It leaves the result unchanged when:

- the task has no `web_search` query;
- the page is a JSON API response;
- no passage matches the query;
- the reranker errors or times out;
- the rewrite would be smaller than 75% of Hermes's window.

It also points the footer's `read_file` hint at the line after its own, shorter head.

Each page logs one line on `plugins.web_extract_rerank` with its `outcome=`.

Limits (module constants): 6 s per rerank call, 10 s per tool result, 12 passages of up to 900 chars.

Self-check: `python3 -I test_web_extract_rerank.py`.

## Install and enable

Copy this directory to the shared plugin directory on the Hermes PVC, next to memini:

```sh
tar -cf - --exclude=__pycache__ web-extract-rerank | kubectl -n ai exec -i <hermes-pod> -c app -- tar -xf - -C /opt/data/plugins
```

Add `web-extract-rerank` to `plugins.enabled` in `/opt/data/config.yaml` (main) and in
`/opt/data/profiles/<profile>/config.yaml` for `homelab-researcher` and `kubernetes-operator`. Restart Hermes
in a quiet window: a restart cuts in-flight cron runs.
