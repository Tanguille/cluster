"""Offline checks for the satellite delivery fix: python3 -I test_satellite_delivery_fix.py"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("satellite_delivery_fix", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class Shared:
    def __init__(self, primary, routes):
        self.primary, self.routes = primary, routes


def original(job, platform, platform_name, target, adapters, config):
    """Stands in for upstream: only a SharedRouteAdapters (or an owned discord adapter) resolves."""
    if isinstance(adapters, Shared) or "discord" in (adapters or {}):
        return ("transport", adapters), None
    return None, f"platform '{platform_name}' not configured/enabled"


def check():
    primary, routes = {"discord": object()}, ["route"]
    resolve = plugin._wrap(original, Shared, lambda: routes, lambda: primary)
    job = {"id": "f020ea3b6598"}

    # The #139825 shape: a satellite whose map holds only its local session adapter.
    resolved, err = resolve(job, "discord", "discord", {}, {"local": object()}, None)
    assert err is None and isinstance(resolved[1], Shared) and resolved[1].primary is primary

    # An empty map already takes upstream's shared path; the wrapper must not touch it.
    assert resolve(job, "discord", "discord", {}, {}, None)[0] is None

    # A profile with its own bot for the target platform keeps its own failure.
    assert resolve(job, "telegram", "telegram", {}, {"local": 1, "telegram": 1}, None)[0] is None

    # Its own bot on another platform does not block a routed Discord delivery.
    assert isinstance(resolve(job, "discord", "discord", {}, {"local": 1, "telegram": 1}, None)[0][1], Shared)

    # No live default adapters (worker subprocess) or no route: unchanged failure.
    assert plugin._wrap(original, Shared, lambda: routes, lambda: None)(
        job, "discord", "discord", {}, {"local": 1}, None)[0] is None
    assert plugin._wrap(original, Shared, lambda: [], lambda: primary)(
        job, "discord", "discord", {}, {"local": 1}, None)[0] is None

    # Enabled platform block but no adapter: upstream returns transport None (standalone path); reroute it.
    def standalone(job, platform, platform_name, target, adapters, config):
        if isinstance(adapters, Shared):
            return ("transport", adapters), None
        return (None, "pconfig", None, adapters), None
    assert isinstance(plugin._wrap(standalone, Shared, lambda: routes, lambda: primary)(
        job, "discord", "discord", {}, {"local": 1}, None)[0][1], Shared)

    # A retry without a live transport keeps the original result.
    dead = lambda *a: ((None, "pconfig", None, a[4]), None)
    assert plugin._wrap(dead, Shared, lambda: routes, lambda: primary)(
        job, "discord", "discord", {}, {"local": 1}, None)[0][3] == {"local": 1}
    print("ok")


if __name__ == "__main__":
    check()
