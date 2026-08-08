# Review quality rules

- Before flagging a config, env var, or resource as missing, read the file that would contain it. If it stays unverifiable, file it under Unknowns at info severity, never as a blocker or major.
- Read a changed config file before calling any line in it correct. A regex, selector or template can be truncated in ways the diff hunk alone does not reveal.
- Do not spend tool calls on `git status`, `git diff --stat` or `git diff --name-only`. The diff and the changed-file list are already in the corpus.
- Never write "confirmed" for a source you did not read. Cite what you fetched this run, or what the previous review's evidence section recorded. On a failed or empty fetch write "not verified" and list the gap under Unknowns.
- For version bumps spanning multiple releases, enumerate every release in the old-to-new range (releases or compare API), and flag when a chart bump moves the embedded appVersion across a major version.
- Translate upgrade steps written for docker-compose or .env files into their Kubernetes/Helm equivalent before flagging them as missing.
- Inline findings must require action. Never post praise as a finding.
- Omit any section you have nothing to report in, Tool Harness Findings and Release notes included. Never write a section whose only content is that it does not apply. Unknowns is the one exception: keep it whenever evidence is incomplete.
- On a PR that only changes a version, digest or pin, the whole review is the recommendation plus one line per changed file.
