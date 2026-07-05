#!/usr/bin/env bash
# Assert that a given context id is present in `meroctl context ls` on the
# TEE replica node — i.e. the replica REPLICATED the context (Open-subgroup
# inheritance / namespace context registration), not merely got authz to it.
#
# Used by the tee-g2 / tee-matrix-* / tee-r1 scenarios as a `script` step with
# `target: local`. Workflow vars are exported to the env uppercased; we also
# accept them positionally for clarity.
#
# Replication is asynchronous (fleet-join announce → namespace-subscribed
# owner → gossip → replica apply), so this POLLS rather than checking once:
# a single-shot check after a fixed `wait` turns any run where replication
# lands past that wait into a hard failure — a timing race that fails
# repeatedly under CI/docker load while passing locally, not a real bug.
# Polling passes as soon as the context appears and only fails if it never
# arrives within the budget.
#
# Args:
#   $1  TEE replica admin/RPC URL   (e.g. http://localhost:7181)
#   $2  context id to look for
#   $3  timeout seconds             (optional, default 90)
#   $4  poll interval seconds       (optional, default 3)
#
# Requires `meroctl` on PATH (the locally-built one — run merobox with the
# core target/release dir prepended to PATH, as the native run command does).

set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <tee_api_url> <context_id> [timeout_s] [interval_s]" >&2
    exit 2
fi
TEE_API="$1"
CTX_ID="$2"
TIMEOUT="${3:-90}"
INTERVAL="${4:-3}"

echo "Polling contexts on TEE replica at ${TEE_API} for ${CTX_ID} (timeout ${TIMEOUT}s) ..."

elapsed=0
last_out=""
while :; do
    # A transient RPC error (node still coming up) is not fatal mid-poll;
    # keep trying until the budget is spent.
    if last_out="$(meroctl --api "${TEE_API}" --output-format json context ls 2>&1)"; then
        if printf '%s' "${last_out}" | grep -q "${CTX_ID}"; then
            echo "OK: context ${CTX_ID} is present on the TEE replica after ${elapsed}s (replicated)."
            exit 0
        fi
    fi

    if [ "${elapsed}" -ge "${TIMEOUT}" ]; then
        break
    fi
    sleep "${INTERVAL}"
    elapsed=$((elapsed + INTERVAL))
done

echo "FAIL: context ${CTX_ID} NOT found on the TEE replica within ${TIMEOUT}s." >&2
echo "Last context ls output:" >&2
echo "${last_out}" >&2
exit 1
