#!/usr/bin/env bash
# Every installer ref a node config names must already be published and anonymously pullable.
#
# Merging a bump makes `pinned` (<talos>-k<kernel>) name a tag the ~2h45m build has not produced
# yet, and nothing else catches it: tuppr's guards go unreachable on a locally imaged node
# (README.md "The rollout is not self-closing"), so a missing tag surfaces as a node that quietly
# stays on its old kernel, or an `apply-node` that writes an unpullable ref.
#
# Needs no age key and no registry credentials -- deliberately: the machine config carries no
# credentials either, so the only meaningful question is whether an ANONYMOUS pull works. Do not
# "simplify" this to `crane manifest`, which silently uses ~/.docker/config.json and reports
# success on a private package.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Same two sources the render path uses, so this cannot check a different tag than it renders.
pinned="$(just tuppr-version talos)-k$(just kernel-version)"

# The refs live literally in the node templates; only the tag is templated. Read them from there
# rather than rendering, which would need the secrets bundle. `:{{ pinned }}` alone identifies
# them -- matching the repo path too would go green if a repo is ever renamed.
#
# Per file, not over the union: every node must repoint .machine.install.image at this registry
# (README.md "The rollout is not self-closing"), so a file yielding nothing is either that
# requirement broken or the grep no longer matching. Checked across the union, one template
# reformatted to `{{pinned}}` would drop out of `sort -u` while the others still matched, and
# that node's ref would go unverified with the check still green.
refs=""
for f in "${REPO_ROOT}"/talos/nodes/*/*.yaml.j2; do
    # || true so an empty result reaches the check below with a filename, rather than exiting
    # on grep's no-match status with no output at all.
    ref="$(grep -oP '^\s+image:\s*\K\S+(?=:\{\{ pinned \}\})' "${f}" || true)"
    [[ -n "${ref}" ]] || { echo "no pinned install image in ${f}" >&2; exit 1; }
    refs+="${ref}"$'\n'
done
# Trailing newline stripped before the herestring adds its own, or the blank line survives
# `sort -u` and the loop below asks the registry for an empty repo.
refs="$(sort -u <<<"${refs%$'\n'}")"

rc=0
while IFS= read -r ref; do
    repo="${ref#ghcr.io/}"
    # Anonymous pull token. ghcr issues one for a private repo too -- it just does not authorize
    # the manifest -- so the token call succeeding proves nothing; only the manifest status does.
    token="$(curl -sf --get --data-urlencode "scope=repository:${repo}:pull" \
        --data-urlencode "service=ghcr.io" https://ghcr.io/token | jq -er '.token')"
    code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${token}" \
        -H 'Accept: application/vnd.oci.image.index.v1+json' \
        -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
        "https://ghcr.io/v2/${repo}/manifests/${pinned}")"
    case "${code}" in
        200) echo "ok        ${ref}:${pinned}" ;;
        403 | 401) echo "PRIVATE   ${ref}:${pinned} exists but is not anonymously pullable; flip the package public" >&2; rc=1 ;;
        *) echo "MISSING   ${ref}:${pinned} (HTTP ${code}) -- build it before merging: gh workflow run talos-kernel.yaml --ref \"\$(git branch --show-current)\"" >&2; rc=1 ;;
    esac
done <<<"${refs}"

exit "${rc}"
