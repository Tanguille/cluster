#!/usr/bin/env bash
# Every variable a Talos template references must be supplied, and everything supplied must be
# used. Both directions fail silently otherwise: minijinja --strict only fires at render time on
# a workstation, and an orphaned -D survives indefinitely (`schematic` did, until #4615).
#
# Needs no age key. sops encrypts values and leaves the key structure in cleartext, so the
# secrets bundle can be read for its shape alone.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
TALOS="${REPO_ROOT}/talos"

# Dotted leaf paths of the secrets bundle. Leaves only: a template references `certs.os.crt`,
# never the `certs.os` map above it.
available="$(yq -r '.. | select(tag != "!!map" and tag != "!!seq") | path | join(".")' \
    "${TALOS}/talsecret.sops.yaml" | grep -v '^sops' | sort -u)"
[[ -n "${available}" ]] || {
    echo "no keys read from talsecret.sops.yaml" >&2
    exit 1
}

# Names the render context injects, read from the -D flags themselves so this cannot drift.
# Both files inject: .justfile passes the versions, talos/mod.just the cluster-wide addresses.
injected="$(grep -hoP -- '-D "\K[a-zA-Z_][a-zA-Z0-9_]*' \
    "${REPO_ROOT}/.justfile" "${TALOS}/mod.just" | sort -u)"
[[ -n "${injected}" ]] || {
    echo "no -D flags found" >&2
    exit 1
}

templates=("${TALOS}"/*.yaml.j2 "${TALOS}"/nodes/*/*.yaml.j2)

# {% set x = %} / {% for x in %} bind locally and are not context variables. Often empty, so the
# filter below is applied conditionally -- `grep -vxF ""` matches every line and would empty the set.
local_names="$(grep -hoP '\{%-? *(?:set|for) +\K[a-zA-Z_][a-zA-Z0-9_]*' "${templates[@]}" | sort -u || true)"

used="$(grep -hoP '\{\{-? *\K[a-zA-Z_][a-zA-Z0-9_.]*' "${templates[@]}" | sort -u)"
[[ -n "${local_names}" ]] && used="$(grep -vxF "${local_names}" <<<"${used}" || true)"
[[ -n "${used}" ]] || {
    echo "no {{ }} references found under talos/" >&2
    exit 1
}

rc=0
while IFS= read -r var; do
    grep -qxF "${var}" <<<"${available}" && continue
    grep -qxF "${var%%.*}" <<<"${injected}" && continue
    echo "UNSUPPLIED  {{ ${var} }} is referenced but neither injected nor in the secrets bundle" >&2
    rc=1
done <<<"${used}"

while IFS= read -r var; do
    grep -qE "^${var}(\.|$)" <<<"${used}" && continue
    echo "ORPHANED    -D ${var} is injected but no template references it" >&2
    rc=1
done <<<"${injected}"

((rc)) || echo "ok: $(wc -l <<<"${used}") references resolve, $(wc -l <<<"${injected}") injected and all used"
exit "${rc}"
