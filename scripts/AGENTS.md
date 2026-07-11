# Script Guidance

Applies to all files under `scripts/`. Also follow the repository-root `AGENTS.md`.

- Start Bash scripts with `set -euo pipefail`.
- Validate shell changes with `mise exec -- shellcheck scripts/*.sh` before declaring work complete.
