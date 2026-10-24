# Hermes cron TL;DR trim

Cron prompts ask for a reply that starts with `**TL;DR:**` or a heading, but the local model often writes its working
notes first and the scheduler posts the whole reply. On `transform_llm_output` for `platform == "cron"`, the plugin
drops the lines above the first TL;DR or `#`-`###` heading line. It runs before the reply is stored, so the session
history matches the post.

Self-check: `python3 -I test_cron_tldr_trim.py`.

## Install and enable

Hooks fire only in the profile that loaded the plugin. Copy `__init__.py` and `plugin.yaml` to
`/opt/data/plugins/cron-tldr-trim/`, symlink that directory into `/opt/data/profiles/<profile>/plugins/` for every
profile with cron jobs (as memini is), and run `hermes [-p <profile>] plugins enable cron-tldr-trim` for each.
Restart Hermes in a quiet window: a restart cuts in-flight cron runs.
