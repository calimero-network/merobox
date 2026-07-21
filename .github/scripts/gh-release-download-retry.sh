#!/usr/bin/env bash
# Retry `gh release download` against transient GitHub asset-API failures.
# The releases/assets/... endpoint intermittently returns HTTP 5xx / 504
# ("We couldn't respond to your request in time") in CI; a single attempt then
# fails the whole job. Retry with linear backoff.
#
# Usage: gh-release-download-retry.sh <repo> <tag> <pattern> <output>
# Env:   RETRY_ATTEMPTS (default 5), GH_TOKEN (as required by gh)
set -euo pipefail

repo="${1:?usage: gh-release-download-retry.sh <repo> <tag> <pattern> <output>}"
tag="${2:?missing tag}"
pattern="${3:?missing pattern}"
output="${4:?missing output}"
attempts="${RETRY_ATTEMPTS:-5}"

for i in $(seq 1 "$attempts"); do
  if gh release download "$tag" --repo "$repo" \
       --pattern "$pattern" --output "$output" --clobber; then
    echo "✓ downloaded $pattern from $repo@$tag (attempt $i/$attempts)"
    exit 0
  fi
  if [ "$i" -lt "$attempts" ]; then
    delay=$((i * 5))
    echo "attempt $i/$attempts failed for $pattern; retrying in ${delay}s..." >&2
    sleep "$delay"
  fi
done

echo "::error::gh release download failed after $attempts attempts: $pattern ($repo@$tag)" >&2
exit 1
