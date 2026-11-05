#!/bin/bash
set -euo pipefail

: "${SOURCE_URL:?}" "${TARGET_URL:?}" "${CACHE_DIR:?}" "${FORGEJO_USER:?}" "${FORGEJO_TOKEN:?}"
export GIT_TERMINAL_PROMPT=0

git init --bare "$CACHE_DIR"
# Only the disposable source cache is force-updated; Forgejo history is never rewritten.
git -C "$CACHE_DIR" fetch --prune "$SOURCE_URL" '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'

# Scope the credential to this destination, without putting it in a URL, argv or the cache.
authorization="$(printf '%s:%s' "$FORGEJO_USER" "$FORGEJO_TOKEN" | base64 | tr -d '\n')"
# A rebased feature branch must not block a valid main update; Git rejects unsafe refs individually.
GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="http.${TARGET_URL}.extraHeader" \
    GIT_CONFIG_VALUE_0="Authorization: Basic $authorization" \
    git -C "$CACHE_DIR" push "$TARGET_URL" 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'
