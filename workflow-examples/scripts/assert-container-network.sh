#!/bin/sh
#
# Assert that a Docker container is (or is NOT) attached to a given network.
# Used by workflow-fault-injection-example.yml to verify that
# disconnect_node / connect_node actually changed the container's network
# membership — without this, a silent no-op in those steps would let the
# workflow pass while the partition test was a sham.
#
#     - name: Verify node 3 is off the bridge
#       type: script
#       target: local
#       script: ./workflow-examples/scripts/assert-container-network.sh
#       args:
#         - calimero-node-3
#         - bridge
#         - absent     # one of: present | absent
#

set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <container> <network> <present|absent>" >&2
    exit 2
fi

container="$1"
network="$2"
expected="$3"

case "${expected}" in
    present|absent) ;;
    *)
        echo "FAIL: third arg must be 'present' or 'absent', got '${expected}'"
        exit 2
        ;;
esac

# Templating over a map returns each key on its own line; grep -Fx matches
# the full line so 'bridge' doesn't accidentally match 'bridge-staging'.
networks=$(docker inspect \
    --format='{{range $k, $_ := .NetworkSettings.Networks}}{{$k}}
{{end}}' \
    "${container}" 2>/dev/null || true)

if [ -z "${networks}" ] && ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "FAIL: container '${container}' not found"
    exit 1
fi

if echo "${networks}" | grep -Fx -q "${network}"; then
    found=present
else
    found=absent
fi

if [ "${found}" != "${expected}" ]; then
    echo "FAIL: ${container} on network '${network}' is '${found}', expected '${expected}'"
    echo "      attached networks: $(echo "${networks}" | tr '\n' ' ')"
    exit 1
fi

echo "OK: ${container} network '${network}' is '${expected}'"
