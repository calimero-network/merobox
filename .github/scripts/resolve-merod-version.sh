#!/usr/bin/env bash
set -euo pipefail

REPO="${1:?Usage: resolve-merod-version.sh <repo> <asset-name>}"
ASSET_NAME="${2:?Usage: resolve-merod-version.sh <repo> <asset-name>}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required to resolve releases" >&2
  exit 1
fi

releases_json="$(gh api "repos/${REPO}/releases?per_page=50")"

if [ -z "$releases_json" ]; then
  echo "No releases found for ${REPO}" >&2
  exit 1
fi

# "edge" prefers prereleases (e.g., -rc) before stable tags.
tag="$(
  echo "$releases_json" | jq -r --arg asset "$ASSET_NAME" '
    [
      .[]
      | select(.draft == false)
      | {
          tag: .tag_name,
          prerelease: .prerelease,
          assets: (.assets // [] | map(.name))
        }
    ]
    | (map(select(.prerelease == true)) + map(select(.prerelease == false)))
    | map(select(.assets | index($asset)))
    | .[0].tag // empty
  '
)"

if [ -z "$tag" ]; then
  echo "No release found with asset ${ASSET_NAME} in ${REPO}" >&2
  exit 1
fi

echo "$tag"
