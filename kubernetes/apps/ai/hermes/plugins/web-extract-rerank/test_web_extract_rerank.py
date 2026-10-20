"""Small offline checks for the Hermes web extract rerank plugin: python3 -I test_web_extract_rerank.py"""

import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("web_extract_rerank", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

PATH = "/opt/data/cache/web/example-0123456789.md"


class Context:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def truncated(full_text, limit):
    """web_extract content as Hermes v0.22.2 _truncate_with_footer builds it."""
    head_budget = int(limit * 0.75)
    head, tail = full_text[:head_budget], full_text[-(limit - head_budget):]
    if (nl := head.rfind("\n")) > head_budget * 0.5:
        head = head[:nl]
    return (head + "\n\n[... middle omitted — see footer ...]\n\n" + tail + "\n\n" +
            "──────── [TRUNCATED] ────────\n" +
            f"Showing {len(head):,} chars (head) + {len(tail):,} chars (tail) of {len(full_text):,} total clean characters.\n" +
            f"Full text saved to: {PATH}\n" +
            f'To read the omitted middle: read_file path="{PATH}" offset={head.count(chr(10)) + 2} limit=200  (the file is the complete page; raise/lower offset to page through it).\n' +
            "─────────────────────────────")


def check():
    ctx = Context()
    plugin.register(ctx)
    assert set(ctx.hooks) == {"post_tool_call", "transform_tool_result"}
    post, transform = ctx.hooks["post_tool_call"], ctx.hooks["transform_tool_result"]

    full_text = "\n\n".join(
        f"## Section {i}\n" + (("rare orchid habitat details " if i in (13, 18) else f"unrelated subject {i} ") * 27)
        for i in range(1, 40))
    original = truncated(full_text, 15000)
    source = {"metadata": {"kept": True}, "results": [
        {"url": "https://example.test/page", "title": "Example", "content": original, "extra": "unchanged"},
        {"url": "https://example.test/small", "content": "short"}]}

    # No web_search in this task yet: unchanged.
    assert transform(tool_name="web_extract", result=json.dumps(source), task_id="t1") is None
    post(tool_name="web_search", args={"query": "rare orchid habitat"}, task_id="t1")
    assert plugin._query("t1") == "rare orchid habitat"
    assert transform(tool_name="read_file", result="x", task_id="t1") is None

    def reverse_rank(query, documents, timeout):
        assert query == "rare orchid habitat" and len(documents) <= 12
        return list(reversed(range(len(documents))))

    with patch.object(plugin, "_read_page", return_value=full_text), patch.object(plugin, "_rerank", side_effect=reverse_rank):
        transformed = transform(tool_name="web_extract", result=json.dumps(source), task_id="t1", status="success")
    assert transformed is not None
    parsed = json.loads(transformed)
    rewritten = parsed["results"][0]["content"]
    # Never larger, and never much smaller: the head fills what the passages leave.
    assert 0.9 * len(original) <= len(rewritten) <= len(original), (len(rewritten), len(original))
    for line in original.splitlines():
        if line.startswith(("Full text saved to:", "──────── [TRUNCATED]")):
            assert line in rewritten, line
    # The read_file hint points at the line after the new, shorter head.
    head_text = rewritten.split("\n\n[... lines", 1)[0]
    assert f'read_file path="{PATH}" offset={head_text.count(chr(10)) + 2} limit=200' in rewritten
    assert parsed["results"][0]["extra"] == "unchanged" and parsed["metadata"] == source["metadata"]
    assert parsed["results"][1] == source["results"][1]
    assert "chosen by relevance to the search query" in rewritten and rewritten.endswith(plugin._NOTE)
    starts = [int(v) for v in re.findall(r"\[\.\.\. lines (\d+)-\d+ of the stored file \.\.\.\]", rewritten)]
    assert starts == sorted(starts) and starts
    head_lines = rewritten.split("\n\n[... lines", 1)[0].count("\n") + 1
    assert starts[0] > head_lines  # passages never repeat the head

    # Minified pages (one huge line) still yield passages beyond the head.
    one_line = " ".join(f"word{i} rare orchid habitat" if i % 50 == 0 else f"filler{i}" for i in range(6000))
    with patch.object(plugin, "_read_page", return_value=one_line), patch.object(plugin, "_rerank", side_effect=reverse_rank):
        flat = transform(tool_name="web_extract", task_id="t1",
                         result=json.dumps({"results": [{"url": "u", "content": truncated(one_line, 15000)}]}))
    assert flat is not None and "chosen by relevance" in json.loads(flat)["results"][0]["content"]

    # Reranker failures and JSON pages leave the result unchanged.
    for error in (RuntimeError("offline"), TimeoutError()):
        with patch.object(plugin, "_read_page", return_value=full_text), patch.object(plugin, "_rerank", side_effect=error):
            assert transform(tool_name="web_extract", result=json.dumps(source), task_id="t1") is None
    api = json.dumps([{"sha": i, "message": "rare orchid habitat"} for i in range(400)], indent=2)
    api_source = {"results": [{"url": "https://api.example.test/x", "content": truncated(api, 15000)}]}
    with patch.object(plugin, "_read_page", return_value=api), patch.object(plugin, "_rerank", side_effect=reverse_rank):
        assert transform(tool_name="web_extract", result=json.dumps(api_source), task_id="t1") is None

    # Stored pages are read from the root or a profile web cache only.
    with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HERMES_HOME": home}):
        for rel in ("cache/web/a.md", "profiles/p1/cache/web/b.md"):
            Path(home, rel).parent.mkdir(parents=True, exist_ok=True)
            Path(home, rel).write_text("ok", encoding="utf-8")
            assert plugin._read_page(str(Path(home, rel))) == "ok"
        for bad in ("config.yaml", "profiles/p1/config.yaml", "../etc/passwd"):
            try:
                plugin._read_page(str(Path(home, bad)))
            except (ValueError, OSError):
                continue
            raise AssertionError(f"read outside the web cache: {bad}")


if __name__ == "__main__":
    check()
    print("web_extract rerank self-check: PASS")
