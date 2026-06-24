#!/usr/bin/env bash
# Assert that a given context id is present in `meroctl context ls` on the
# TEE replica node — i.e. the replica REPLICATED the context (Open-subgroup
# inheritance / namespace context registration), not merely got authz to it.
#
# Used by tee-g2-open-inheritance-replication.yml as a `script` step with
# `target: local`. Workflow vars are exported to the env uppercased; we also
# accept them positionally for clarity.
#
# Args:
#   $1  TEE replica admin/RPC URL  (e.g. http://localhost:7181)
#   $2  context id to look for
#
# Requires `meroctl` on PATH (the locally-built one — run merobox with the
# core target/release dir prepended to PATH, as the native run command does).

set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <tee_api_url> <context_id>" >&2
    exit 2
fi
TEE_API="$1"
CTX_ID="$2"

echo "Listing contexts on TEE replica at ${TEE_API} ..."
OUT="$(meroctl --api "${TEE_API}" --output-format json context ls 2>&1)" || {
    echo "meroctl context ls failed:" >&2
    echo "${OUT}" >&2
    exit 1
}

echo "${OUT}"

if printf '%s' "${OUT}" | grep -q "${CTX_ID}"; then
    echo "OK: context ${CTX_ID} is present on the TEE replica (replicated)."
    exit 0
fi

echo "FAIL: context ${CTX_ID} NOT found on the TEE replica." >&2
exit 1
