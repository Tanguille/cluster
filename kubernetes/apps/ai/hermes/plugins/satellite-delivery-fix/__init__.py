"""Route satellite-profile cron delivery through the default bot until hermes-agent #139960/#133138 ships."""

import logging

_LOG = logging.getLogger("plugins.satellite_delivery_fix")


def _live(resolved):
    return resolved is not None and resolved[0] is not None  # transport None = standalone path, no credential here


def _wrap(original, shared_cls, routes_for_home, primary_adapters):
    def resolve(job, platform, platform_name, target, adapters, config):
        resolved, err = original(job, platform, platform_name, target, adapters, config)
        if _live(resolved) or not adapters or isinstance(adapters, shared_cls):
            return resolved, err
        if platform in adapters:
            return resolved, err  # the profile owns this platform's bot (#133138's rule): its own failure stands
        primary, routes = primary_adapters(), routes_for_home()
        if not primary or not routes:
            return resolved, err
        retried, retry_err = original(job, platform, platform_name, target, shared_cls(primary, routes), config)
        _LOG.info("job=%s platform=%s outcome=%s", job.get("id"), platform_name,
                  "rerouted" if _live(retried) else "still-failed")
        return (retried, retry_err) if _live(retried) else (resolved, err)

    return resolve


def _active_primary_adapters():
    from gateway.run_api import _ACTIVE_RUNNER

    return getattr(_ACTIVE_RUNNER, "adapters", None)


def register(ctx):
    from cron import scheduler_delivery, scheduler_preflight

    current = scheduler_delivery._resolve_target_transport
    if getattr(current, "_satellite_delivery_fix", False):
        return
    patched = _wrap(current, scheduler_preflight.SharedRouteAdapters,
                    scheduler_preflight._primary_profile_routes_for_current_home, _active_primary_adapters)
    patched._satellite_delivery_fix = True
    scheduler_delivery._resolve_target_transport = patched
    _LOG.info("wrapped cron.scheduler_delivery._resolve_target_transport")
