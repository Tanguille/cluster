#!/usr/bin/env python3
"""One /metrics scrape shared by the bench scripts that need spec-decode counters.

The rest of this directory is deliberately standalone single-file scripts. This
exists because walltime.py and toolbench.py otherwise carry byte-identical
copies of the same scrape, and because the naive shape cost three full scrapes
per rep (poll for idle, re-read idle for the clean flag, read the counters).
The vLLM /metrics payload is hundreds of series and every read crosses a
kubectl port-forward -- which wedged repeatedly in practice -- so one call
returning everything is both less code and less load.
"""
import time
import urllib.request

SPEC = "vllm:spec_decode_num_"


class Sample:
    """running/waiting plus the three spec-decode counters, from one scrape."""

    __slots__ = ("running", "waiting", "drafts", "draft_tokens", "accepted")

    def __init__(self, running=0.0, waiting=0.0, drafts=0.0, draft_tokens=0.0,
                 accepted=0.0):
        self.running = running
        self.waiting = waiting
        self.drafts = drafts
        self.draft_tokens = draft_tokens
        self.accepted = accepted

    @property
    def idle(self):
        return self.running == 0 and self.waiting == 0


def sample(port, timeout=8):
    """Scrape once. Spec counters read 0 when speculative decoding is off."""
    s = Sample()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics",
                                timeout=timeout) as r:
        for ln in r.read().decode().splitlines():
            if ln.startswith("#"):
                continue
            try:
                v = float(ln.rsplit(" ", 1)[-1])
            except ValueError:
                continue
            if ln.startswith("vllm:num_requests_running{"):
                s.running += v
            elif ln.startswith("vllm:num_requests_waiting{"):
                s.waiting += v
            elif ln.startswith(SPEC + "drafts_total"):
                s.drafts = v
            elif ln.startswith(SPEC + "draft_tokens_total"):
                s.draft_tokens = v
            elif ln.startswith(SPEC + "accepted_tokens_total"):
                s.accepted = v
    return s


def wait_idle(port, tries=60, delay=5):
    """Block until the engine is quiet, then return that same Sample.

    Returns the scrape that proved idleness so the caller can use it as the
    pre-run counter baseline instead of scraping again. Benchmarks must gate on
    0 running / 0 waiting -- production traffic contaminates the numbers.
    """
    s = sample(port)
    for _ in range(tries):
        if s.idle:
            return s
        time.sleep(delay)
        s = sample(port)
    return s
