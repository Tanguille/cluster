"""Offline checks for the cron TL;DR trim: python3 -I test_cron_tldr_trim.py"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cron_tldr_trim", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class Context:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def check():
    ctx = Context()
    plugin.register(ctx)
    hook = ctx.hooks["transform_llm_output"]

    # 3d-prints 10-24: a budget note above the TL;DR.
    report = "**TL;DR:** Today's best pick: Screw Snatcher\nMagnetic screw pickup."
    assert hook(response_text="584 chars, 5 lines — within budget. Delivering.\n\n" + report, platform="cron") == report
    # GitOps digest 10-24: narration above a heading; the heading stays.
    digest = "## 🏠 GitOps digest: 2026-10-24\n**TL;DR:** 🟡 external-dns v0.24.0 is breaking."
    assert hook(response_text="Memory recorded.\n\nVerification summary:\n- a\n- b\n\n" + digest, platform="cron") == digest
    # The scheduler's skipped-skill notice survives, the narration between it and the heading does not.
    out = hook(response_text="⚠️ Skill(s) not found and skipped: google-workspace\nAll data gathered.\n## ☀️ Good morning",
               platform="cron")
    assert out == "⚠️ Skill(s) not found and skipped: google-workspace\n## ☀️ Good morning", out

    # Nothing above the contract line, no contract line at all, [SILENT], or a chat reply: left alone.
    assert hook(response_text=report, platform="cron") is None
    assert hook(response_text="On this day in 1901, Annie Edson Taylor rode the falls.", platform="cron") is None
    assert hook(response_text="[SILENT]", platform="cron") is None
    assert hook(response_text="[CRON_FAILURE]\nchild run failed\n## Digest\n**TL;DR:** partial", platform="cron") is None
    assert hook(response_text="Sure.\n**TL;DR:** chat", platform="discord") is None
    # Discord subtext (-#) is not a heading.
    assert hook(response_text="note\n-# Sources: x", platform="cron") is None
    print("ok")


if __name__ == "__main__":
    check()
