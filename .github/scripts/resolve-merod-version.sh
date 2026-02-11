#!/usr/bin/env bash
set -euo pipefail

REPO="${1:?Usage: resolve-merod-version.sh <repo> <asset-name>}"
ASSET_NAME="${2:?Usage: resolve-merod-version.sh <repo> <asset-name>}"

releases_json="$(gh release list \
  --repo "$REPO" \
  --exclude-drafts \
  --limit 50 \
  --json tagName,isPrerelease,createdAt \
  --jq '.[] | [.tagName, .isPrerelease, .createdAt] | @tsv')"

if [ -z "$releases_json" ]; then
  echo "No releases found for ${REPO}" >&2
  exit 1
fi

prerelease_tags=()
stable_tags=()

while IFS=$'\t' read -r tag is_prerelease _created_at; do
  if [ "$is_prerelease" = "true" ]; then
    prerelease_tags+=("$tag")
  else
    stable_tags+=("$tag")
  fi
done <<< "$releases_json"

candidate_tags=("${prerelease_tags[@]}" "${stable_tags[@]}")

for tag in "${candidate_tags[@]}"; do
  asset_exists="$(gh release view "$tag" \
    --repo "$REPO" \
    --json assets \
    --jq --arg name "$ASSET_NAME" 'any(.assets[].name; . == $name)')"

  if [ "$asset_exists" = "true" ]; then
    echo "$tag"
    exit 0
  fi
done

echo "No release found with asset ${ASSET_NAME} in ${REPO}" >&2
exit 1
