#!/bin/sh
#
# Assert that a Docker container is in an expected lifecycle state.
# Used by workflow-fault-injection-example.yml to verify that pause /
# unpause / restart actually changed the container state — a silent no-op
# in those steps would otherwise let the workflow pass while exercising
# nothing.
#
#     - name: Verify node 1 is paused
#       type: script
#       target: local
#       script: ./workflow-examples/scripts/assert-container-state.sh
#       args:
#         - calimero-node-1
#         - paused
#

set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <container> <expected-state>" >&2
    echo "expected-state: one of paused | running | exited | created | restarting | dead" >&2
    exit 2
fi

container="$1"
expected="$2"

actual=$(docker inspect --format='{{.State.Status}}' "${container}" 2>/dev/null || true)

if [ -z "${actual}" ]; then
    echo "FAIL: container '${container}' not found"
    exit 1
fi

if [ "${actual}" != "${expected}" ]; then
    echo "FAIL: ${container} state is '${actual}', expected '${expected}'"
    exit 1
fi

echo "OK: ${container} state == '${expected}'"
