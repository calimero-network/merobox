#!/usr/bin/env bash
# Assert that the TEE replica is an INHERITED-ONLY member of an Open subgroup:
# it has NO direct group identity row for that subgroup, so a node-local
# `meroctl group members list <open_gid>` on the replica fails with
# "no group identity configured" (or returns no direct row) even though the
# replica is functionally a member via namespace-root inheritance.
#
# This documents calimero-network/core#2771 (born-Open atomic create not yet
# landed): an Open subgroup never materialises a direct ReadOnlyTee row for an
# inherited member, so the local member-list lookup has nothing to key on.
#
# EXPECTED OUTCOME: this script FAILS (non-zero) until #2771 lands. The
# workflow tee-r2-open-no-direct-row.yml is therefore an EXPECTED-RED scenario.
#
# Args:
#   $1  TEE replica admin/RPC URL  (e.g. http://localhost:7181)
#   $2  Open subgroup id
#
# Requires `meroctl` on PATH.

set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <tee_api_url> <open_group_id>" >&2
    exit 2
fi
TEE_API="$1"
OPEN_GID="$2"

echo "Listing members of Open subgroup ${OPEN_GID} on TEE replica at ${TEE_API} ..."
set +e
OUT="$(meroctl --api "${TEE_API}" --output-format json group members list "${OPEN_GID}" 2>&1)"
RC=$?
set -e

echo "${OUT}"

# The pre-#2771 reality: a pure inherited member has no group identity
# configured locally, so the lookup errors. We assert the replica DOES show a
# direct ReadOnlyTee row (which it does NOT, today) — so this script is
# expected to FAIL, proving the gap.
if [ "${RC}" -eq 0 ] && printf '%s' "${OUT}" | grep -q "ReadOnlyTee"; then
    echo "UNEXPECTED PASS: replica has a direct ReadOnlyTee row for the Open subgroup."
    echo "  -> born-Open atomic create (#2771) may have landed; update this scenario."
    exit 0
fi

echo "EXPECTED FAIL: no direct ReadOnlyTee row for the Open subgroup on the replica" >&2
echo "  (inherited-only membership; born-Open atomic create #2771 not yet landed)." >&2
exit 1
