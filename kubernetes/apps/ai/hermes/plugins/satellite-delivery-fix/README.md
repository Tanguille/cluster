# Hermes satellite delivery fix

Workaround for [hermes-agent#139825](https://github.com/NousResearch/hermes-agent/issues/139825), a v0.22.3
regression: once a satellite profile (no Discord token of its own, routed through `gateway.profile_routes`) has
had any local session, its adapter map holds a `LocalSessionAdapter`. Both cron delivery paths then skip the
default profile's bot and fail with `platform 'discord' not configured/enabled`.

The plugin wraps `cron.scheduler_delivery._resolve_target_transport`. When a lookup fails and the profile holds
no adapter for the target platform (#133138's rule), it retries once through the default profile's live adapters,
authorized by the same `profile_routes` the upstream empty-map path uses. A thread without a route still fails
closed. Each retry logs one line on `plugins.satellite_delivery_fix` with its `outcome=`. Manual `cron run` and
satellite `send_message` go through `_adapters_for_profile` instead and stay broken (#139960 fixes that site).

Remove it once [#139960](https://github.com/NousResearch/hermes-agent/pull/139960) or
[#133138](https://github.com/NousResearch/hermes-agent/pull/133138) ships in a release.

Self-check: `python3 -I test_satellite_delivery_fix.py`.

## Install and enable

Copy `__init__.py` and `plugin.yaml` to `/opt/data/plugins/satellite-delivery-fix/` on the Hermes PVC and add
`satellite-delivery-fix` to `plugins.enabled` in `/opt/data/config.yaml` (main only: the patch is process-wide).
Restart Hermes in a quiet window: a restart cuts in-flight cron runs.
