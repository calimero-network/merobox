#!/bin/bash
# Run a merobox workflow with retry logic.
# Usage: run_with_retry.sh <workflow_file> [--no-docker]
set -euo pipefail

WORKFLOW_FILE="$1"
shift
EXTRA_ARGS="$@"

MAX_ATTEMPTS=3
WORKFLOW_NAME=$(basename "$WORKFLOW_FILE" .yml)

# Trust only the examples that need it, so the rest exercise the untrusted guards.
TRUST=""
if python3 "$(dirname "$0")/needs_trust.py" "$WORKFLOW_FILE" $EXTRA_ARGS; then
    TRUST="--trust-workflow"
fi

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if [ $attempt -gt 1 ]; then
        echo "Retry $attempt/$MAX_ATTEMPTS for $WORKFLOW_NAME..."
        merobox stop --all $EXTRA_ARGS || true
        merobox nuke -f || true
        sleep 2
    fi

    if merobox bootstrap run "$WORKFLOW_FILE" $EXTRA_ARGS --e2e-mode $TRUST; then
        echo "✅ $WORKFLOW_NAME passed"
        merobox stop --all $EXTRA_ARGS || true
        merobox nuke -f || true
        exit 0
    fi
done

merobox stop --all $EXTRA_ARGS || true
merobox nuke -f || true
echo "❌ $WORKFLOW_NAME failed after $MAX_ATTEMPTS attempts"
exit 1
