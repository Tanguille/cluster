# Local review

- Review `git diff origin/main...` plus staged, unstaged and untracked files
  with the sections of [SKILL.md](../SKILL.md).
- To carry a PR to green CI and a clean Kritika review, follow
  [pr-shepherd.md](pr-shepherd.md).

## crane

- `crane ls <registry>/<repo>` lists tags.
- `crane digest <image>:<tag>` resolves a tag.
- `crane manifest <image>:<tag>` lists platforms.
- `crane config --platform linux/amd64 <image>@<digest>` shows `created` and
  the source and revision labels.
- `crane config <chart>:<version>` prints a chart's `Chart.yaml` as JSON:
  `version`, `appVersion`, `sources`.
