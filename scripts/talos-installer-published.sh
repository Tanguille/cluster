#!/usr/bin/env bash
# Every installer ref a node config names must already be published and anonymously pullable.
#
# Merging a bump makes `pinned` (<talos>-k<kernel>) name a tag the ~2h45m build has not produced
# yet, and nothing else catches it: tuppr's guards are unreachable on a locally imaged node
# (README.md "The rollout is not self-closing"), so a missing tag surfaces as a node that quietly
# stays on its old kernel, or an `apply-node` that writes an unpullable ref.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Same two sources the render path uses, so this cannot check a different tag than it renders.
pinned="$(just tuppr-version talos)-k$(just kernel-version)"

# An empty DOCKER_CONFIG is the entire point of this check. The machine config carries no registry
# credentials either, so the only meaningful question is whether an ANONYMOUS pull works -- plain
# `crane` reads ~/.docker/config.json and reports success on a package that is still private.
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
trap 'rm -rf "${DOCKER_CONFIG}"' EXIT

rc=0
for f in "${REPO_ROOT}"/talos/nodes/*/*.yaml.j2; do
    node="$(basename "${f}" .yaml.j2)"
    # The ref lives literally in the template; only the tag is templated. Read it from there
    # rather than rendering, which would need the secrets bundle. Per file, because every node
    # must repoint .machine.install.image at this registry: a file yielding nothing is either
    # that requirement broken or the grep no longer matching, and both need a name to chase.
    ref="$(grep -oP '^\s+image:\s*\K\S+(?=:\{\{ pinned \}\})' "${f}" || true)"
    [[ -n "${ref}" ]] || { echo "no pinned install image in ${f}" >&2; exit 1; }
    # crane's own error already separates the two failures worth telling apart:
    # MANIFEST_UNKNOWN (never built) from DENIED (built, but the package is private).
    if err="$(crane manifest "${ref}:${pinned}" 2>&1 >/dev/null)"; then
        echo "ok         ${node}  ${ref}:${pinned}"
    else
        echo "UNPULLABLE ${node}  ${ref}:${pinned}" >&2
        echo "           ${err##*$'\n'}" >&2
        rc=1
    fi
done

exit "${rc}"
