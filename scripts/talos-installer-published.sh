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
# rather than rendering, which would need the secrets bundle.
refs="$(grep -hoP '^\s+image:\s*\K\S+installer/\S+(?=:\{\{ pinned \}\})' \
    "${REPO_ROOT}"/talos/nodes/*/*.yaml.j2 | sort -u)"
[[ -n "${refs}" ]] || { echo "no installer image refs found under talos/nodes/" >&2; exit 1; }

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
