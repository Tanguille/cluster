"""Hold cron replies to their TL;DR-first contract: drop the working notes a model writes above it."""

import re

_START = re.compile(r"\*\*TL;DR:?\*\*|TL;DR:|#{1,3} \S")


def trim(text):
    """Text from the first TL;DR or heading line on, or None when there is nothing above it to drop."""
    lines = text.splitlines()
    # The scheduler reads [CRON_FAILURE] only as the exact first line: a failed run stays as written.
    if not lines or lines[0].strip() == "[CRON_FAILURE]":
        return None
    start = next((i for i, line in enumerate(lines) if _START.match(line.strip())), 0)
    if not start:
        return None
    # The scheduler asks for a "⚠️ Skill(s) not found" notice first: that line is for the user, not a working note.
    notices = [line for line in lines[:start] if line.startswith("⚠️")]
    return "\n".join(notices + lines[start:]).strip()


def _on_output(response_text="", platform="", **_):
    # Only scheduled reports carry the contract; chat keeps its free form.
    return trim(response_text) if platform == "cron" else None


def register(ctx):
    ctx.register_hook("transform_llm_output", _on_output)
