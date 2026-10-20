"""Rerank relevant passages from truncated Hermes web_extract pages."""

import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_LOG = logging.getLogger("plugins.web_extract_rerank")
_URL = "http://bge-reranker-v2-m3.ai.svc.cluster.local:8080/v1/rerank"
_MODEL = "bge-reranker-v2-m3"
# One rerank call per page on a single llama.cpp slot (iGPU) shared with memini, whose own timeout is 5 s:
# 12 passages of ~900 chars took 1.3-3.0 s there on real pages.
_TIMEOUT_S, _BUDGET_S = 6, 10  # per page, per tool result
_MAX_DOCS, _MAX_PAYLOAD_CHARS = 12, 14000
_CHUNK = 900  # passage size; a (query, passage) pair must fit the reranker's 2048-token batch
# Only the task's last web_search query is used: in state.db the user message is mostly kanban stubs
# ("work kanban task t_..."), "[System: Continue now...]" nudges or 14-20K-char skill preambles. The query
# is repeated in every (query, passage) pair, so it stays short.
_MAX_QUERY_CHARS = 400
_MAX_KEYS = 256
_NOTE = "Passages ranked by a cross-encoder; the rest of the page is in the stored file above."
_MARKER_CHARS = 52  # "[... lines N-M of the stored file ...]\n" plus the blank line between passages
_FOOTER = re.compile(r"\n──────── \[TRUNCATED\] ────────\n")
_TOTAL = re.compile(r"of ([\d,]+) total clean characters")
_PATH = re.compile(r"^Full text saved to: (.+)$", re.M)
_SHOWING = re.compile(r"^Showing .*?$", re.M)
_OFFSET = re.compile(r'(read_file path="[^"]*" offset=)\d+')
_TOKENS = re.compile(r"\w+")

_LOCK = threading.Lock()
_QUERIES = OrderedDict()  # task or session id -> last web_search query


def _remember(key, search):
    if key is None or not search:
        return
    with _LOCK:
        _QUERIES.pop(key, None)
        _QUERIES[key] = search[:_MAX_QUERY_CHARS]
        while len(_QUERIES) > _MAX_KEYS:
            _QUERIES.popitem(last=False)


def _query(key):
    with _LOCK:
        value = _QUERIES.get(key, "")
        if value:
            _QUERIES.move_to_end(key)
    return value


def _snap_head(text, limit):
    """Prefix of at most ``limit`` chars, cut at a line break when one is in the back half."""
    head = text[:max(0, limit)]
    nl = head.rfind("\n")
    return head[:nl] if nl > len(head) // 2 else head


def _chunks(text):
    """Passages of whole paragraphs packed up to _CHUNK chars: (first line, last line, char offset, text).

    Packing matters: changelogs and scraped pages put a blank line between most lines, and the cross-encoder
    ranks a bare "Welcome to the v1.14.1 release" line above the paragraphs under it. A short heading leads
    the next paragraph; a paragraph longer than _CHUNK is split on spaces. Lines are counted on "\\n" only,
    as read_file counts them.
    """
    paras, buf, first, start, offset = [], [], 0, 0, 0
    for number, line in enumerate(text.split("\n") + [""], 1):
        if line.strip():
            if not buf:
                first, start = number, offset
            buf.append(line)
        elif buf:
            paras.append((first, number - 1, start, "\n".join(buf)))
            buf = []
        offset += len(line) + 1
    chunks, current = [], None
    for first, last, start, para in paras:
        while para:
            cut = len(para)
            if cut > _CHUNK:
                cut = para.rfind(" ", 0, _CHUNK)
                cut = cut if cut > _CHUNK // 2 else _CHUNK
            piece, rest = para[:cut], para[cut:]
            gap = len(rest) - len(rest.lstrip())
            bare_heading = current and current[3].startswith("#") and len(current[3]) < 100 and "\n" not in current[3]
            if current and not piece.startswith("#") and (bare_heading or len(current[3]) + 2 + len(piece) <= _CHUNK):
                current = (current[0], last, current[2], current[3] + "\n\n" + piece)
            else:
                if current:
                    chunks.append(current)
                current = (first, last, start, piece)
            start += cut + gap
            para = rest[gap:]
    if current:
        chunks.append(current)
    return chunks


def _bm25(query, chunks):
    terms = _TOKENS.findall(query.lower())
    docs = [Counter(_TOKENS.findall(chunk[3].lower())) for chunk in chunks]
    if not terms:
        return [0.0] * len(docs)
    df = Counter(term for term in set(terms) for doc in docs if doc[term])
    avg = sum(sum(doc.values()) for doc in docs) / len(docs) or 1
    scores = []
    for doc in docs:
        length = sum(doc.values())
        score = 0.0
        for term in terms:
            freq = doc[term]
            if freq:
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * length / avg))
        scores.append(score)
    return scores


def _read_page(path):
    # Profiles keep their own cache (<home>/profiles/<name>/cache/web), the root profile <home>/cache/web.
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
    rel = Path(path).resolve().relative_to(home)  # ValueError outside home
    if not (rel.full_match("cache/web/*") or rel.full_match("profiles/*/cache/web/*")):
        raise ValueError("stored path is outside web cache")
    return (home / rel).read_text(encoding="utf-8")


def _rerank(query, documents, timeout):
    payload = json.dumps({"model": _MODEL, "query": query, "documents": documents, "top_n": len(documents)}).encode()
    request = Request(_URL, payload, {"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        ranked = json.loads(response.read().decode("utf-8"))["results"]
    valid = [(int(item["index"]), float(item["relevance_score"])) for item in ranked]
    if any(index < 0 or index >= len(documents) for index, _ in valid):
        raise ValueError("reranker returned invalid document index")
    return [index for index, _ in sorted(valid, key=lambda pair: pair[1], reverse=True)]


def _rewrite(content, query, deadline, counts):
    marker = _FOOTER.search(content)
    footer = content[marker.start() + 1:] if marker else ""
    total_match, path_match = _TOTAL.search(footer), _PATH.search(footer)
    if not total_match or not path_match or not _SHOWING.search(footer):
        return None, "skipped:incomplete-footer"
    counts["total_chars"] = total = int(total_match.group(1).replace(",", ""))
    try:
        full_text = _read_page(path_match.group(1))
    except Exception:
        return None, "skipped:unreadable"
    # API JSON loses its structure when cut into passages, and is token-dense enough to time out the reranker.
    if full_text[:64].lstrip()[:1] in ("{", "["):
        return None, "skipped:json"
    # Room for head + passages: the original size minus the footer, the note and Showing-line growth.
    # Passages get at most 60% of it; the page head takes the rest, so the intro stays and the result
    # does not shrink. Passages come from beyond the head (by char offset: minified pages are a few lines).
    room = len(content) - len(footer) - len(_NOTE) - 64
    passage_room = min(int(room * 0.6), _MAX_DOCS * (_CHUNK + _MARKER_CHARS))
    head = _snap_head(full_text, room - passage_room)
    chunks = _chunks(full_text)
    counts["chunks"] = len(chunks)
    candidates = [chunk for chunk in chunks if chunk[2] >= len(head)]
    if not candidates:
        return None, "skipped:no-candidates"
    scores = _bm25(query, candidates)
    if max(scores) <= 0:
        return None, "skipped:no-match"
    selected, payload_chars = [], len(query)
    for index in sorted(range(len(candidates)), key=lambda i: (-scores[i], i)):
        text = candidates[index][3]
        if len(selected) < _MAX_DOCS and payload_chars + len(text) <= _MAX_PAYLOAD_CHARS:
            selected.append(index)
            payload_chars += len(text)
    counts["sent"] = len(selected)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "timeout"
    started = time.monotonic()
    try:
        ranked = _rerank(query, [candidates[i][3] for i in selected], min(_TIMEOUT_S, remaining))
    except Exception as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        return None, "timeout" if timed_out else "error"
    finally:
        counts["latency_ms"] = int((time.monotonic() - started) * 1000)
    keep, used = [], 0
    for i in ranked:  # best first
        cost = len(candidates[selected[i]][3]) + _MARKER_CHARS
        if used + cost <= passage_room:
            keep.append(selected[i])
            used += cost
    if not keep:
        return None, "skipped:no-passage-fits"
    picked = [candidates[i] for i in sorted(keep)]  # candidates are in page order
    counts["chosen"] = len(picked)
    passages = "\n\n".join(f"[... lines {first}-{last} of the stored file ...]\n{text}" for first, last, _, text in picked)
    showing = (f"Showing {len(head):,} chars (head) + {len(picked)} passages "
               f"({sum(len(chunk[3]) for chunk in picked):,} chars) chosen by relevance to the search query, "
               f"of {total:,} total clean characters.")
    # Hermes's read_file hint points past its own longer head; point it at the line after ours.
    next_line = head.count("\n") + 2
    footer = _OFFSET.sub(lambda m: f"{m.group(1)}{next_line}", _SHOWING.sub(showing, footer, count=1), count=1)
    rewritten = head.rstrip() + "\n\n" + passages + "\n\n" + footer.rstrip() + "\n" + _NOTE
    if len(rewritten) > len(content):
        return None, "skipped:over-budget"
    # Short passages (UI fragments, one-line paragraphs) can leave the result well under Hermes's own window.
    if len(rewritten) < 0.75 * len(content):
        return None, "skipped:underfill"
    return rewritten, "rewritten"


def register(ctx):
    def post_tool_call(tool_name=None, args=None, task_id=None, session_id=None, **kwargs):
        if tool_name == "web_search" and isinstance(args, dict):
            _remember(task_id or session_id, str(args.get("query") or ""))

    def transform_tool_result(tool_name=None, result=None, task_id=None, session_id=None, status=None, **kwargs):
        try:
            if tool_name != "web_extract" or not isinstance(result, str) or "[TRUNCATED]" not in result:
                return None
            if str(status or "").lower() in {"error", "failed", "cancelled"}:
                return None
            data = json.loads(result)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                return None
            query = _query(task_id or session_id)
            deadline = time.monotonic() + _BUDGET_S
            changed = False
            for entry in data["results"]:
                content = entry.get("content") if isinstance(entry, dict) else None
                if not isinstance(content, str) or "[TRUNCATED]" not in content:
                    continue
                counts = Counter()
                if not query:
                    rewritten, outcome = None, "skipped:no-query"
                elif time.monotonic() >= deadline:
                    rewritten, outcome = None, "timeout"
                else:
                    rewritten, outcome = _rewrite(content, query, deadline, counts)
                if rewritten is not None:
                    entry["content"] = rewritten
                    changed = True
                try:
                    host = urlsplit(str(entry.get("url", ""))).hostname or ""
                except ValueError:
                    host = ""
                _LOG.info("host=%s total=%d size=%d chunks=%d sent=%d chosen=%d latency_ms=%d outcome=%s",
                          host, counts["total_chars"], len(content), counts["chunks"], counts["sent"],
                          counts["chosen"], counts["latency_ms"], outcome)
            return json.dumps(data, indent=2, ensure_ascii=False) if changed else None
        except Exception:
            return None

    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("transform_tool_result", transform_tool_result)
