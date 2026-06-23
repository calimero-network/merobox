#!/usr/bin/env bash
# Set the owner's TeeAdmissionPolicy for the test namespace to accept the
# published 2.3.44 mrtd, so when the real fleet node's attestation is
# verified by the owner, the admission policy check passes.
#
# Runs INSIDE the test owner container (script step `target: nodes`).
#
# Env passed by merobox (assumed — see VERIFY notes below):
#   NS                  workflow variable {{ns}} (the test namespace id, hex)
#   PROD_MRTD    inherited from the host env
#
# Merod admin endpoint (verified):
#   PUT /admin-api/groups/{group_id}/settings/tee-admission-policy
#   body: { "allowed_mrtd": ["<hex>"] }
#   (see core/crates/server/src/admin/service.rs:273
#    + core/crates/server/src/admin/handlers/groups/set_tee_admission_policy.rs)
#
# VERIFY before merging:
#   - merobox script step actually exposes workflow vars as env (NS in
#     particular). If not, swap to a templated invocation or args.
#   - merod admin auth posture in the merobox test container. The cone
#     example uses `meroctl` without credentials, suggesting auth is
#     permissive in test containers. If auth-required, this script
#     needs a credential.
#   - merod admin port in merobox: the merobox `nodes.base_port` is
#     9080 in the WF; merod's admin/server port maps to that. Adjust
#     if merobox uses a different mapping.

set -eu

# Workflow var arrives as positional $1 (target: local + args: in the YAML).
# Host env supplies PROD_MRTD (your shell exported it).
if [ "$#" -lt 1 ]; then
    echo "usage: $0 <namespace_id>" >&2
    exit 2
fi
NS="$1"
: "${PROD_MRTD:?PROD_MRTD not set in env}"

# Merod admin is exposed on the host at the RPC/Admin port that merobox
# mapped (see 'RPC/Admin Port: 9180' in the merobox run log).
MEROD_ADMIN="${MEROD_ADMIN:-http://localhost:9180}"

# `printf '%.16s'` is POSIX; ${VAR:0:N} is a bashism the image's sh rejects.
MRTD_PREFIX=$(printf '%.16s' "$PROD_MRTD")
echo "[set-tee-admission-policy] namespace=${NS} mrtd=${MRTD_PREFIX}..."

# merod's admin API serializes/deserializes in camelCase via serde
# `rename_all = "camelCase"` — `allowed_mrtd` on the wire silently fails
# the request shape and the server falls back to its default empty policy,
# then rejects validation. Use `allowedMrtd`.
#
# Also: `curl -sf | tee` swallows HTTP errors in POSIX sh (no pipefail), so
# go through the http_code dance — same pattern as enable-ha-prod.sh after
# Cursor Bugbot caught the same anti-pattern in the fleet sidecar.
HTTP_BODY=$(curl -s -o - -w '\n%{http_code}' -X PUT \
  -H 'Content-Type: application/json' \
  -d "{\"allowedMrtd\": [\"${PROD_MRTD}\"]}" \
  "${MEROD_ADMIN}/admin-api/groups/${NS}/settings/tee-admission-policy")
HTTP_CODE=$(printf '%s' "$HTTP_BODY" | tail -n1)
HTTP_BODY=$(printf '%s' "$HTTP_BODY" | sed '$d')

echo "[set-tee-admission-policy] merod http=${HTTP_CODE} body=${HTTP_BODY}" | tee /tmp/set-tee-admission-policy.out

if [ "$HTTP_CODE" != "200" ]; then
    echo "[set-tee-admission-policy] ERROR: merod returned ${HTTP_CODE} — refusing to continue" >&2
    exit 1
fi

echo "[set-tee-admission-policy] done"
