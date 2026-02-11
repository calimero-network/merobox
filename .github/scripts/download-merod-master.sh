#!/usr/bin/env bash
set -euo pipefail

REPO="${1:?Usage: download-merod-master.sh <repo> <tarball-name> [workflow] [branch]}"
TARBALL_NAME="${2:?Usage: download-merod-master.sh <repo> <tarball-name> [workflow] [branch]}"
WORKFLOW_NAME="${3:-Release}"
BRANCH="${4:-master}"
ARTIFACT_NAME="artifacts-x86_64-unknown-linux-gnu"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required to resolve artifacts" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to extract artifacts" >&2
  exit 1
fi

run_json="$(gh run list \
  -R "$REPO" \
  --workflow "$WORKFLOW_NAME" \
  --branch "$BRANCH" \
  --status success \
  --limit 1 \
  --json databaseId,headSha \
  --jq '.[0]')"

if [ -z "$run_json" ] || [ "$run_json" = "null" ]; then
  echo "No successful ${WORKFLOW_NAME} run found on ${BRANCH} for ${REPO}" >&2
  exit 1
fi

run_id="$(echo "$run_json" | jq -r '.databaseId')"
run_sha="$(echo "$run_json" | jq -r '.headSha')"

if [ -z "$run_id" ] || [ -z "$run_sha" ]; then
  echo "Failed to resolve workflow run metadata for ${REPO}" >&2
  exit 1
fi

artifact_id="$(gh api "repos/${REPO}/actions/runs/${run_id}/artifacts" \
  | jq -r --arg name "$ARTIFACT_NAME" '.artifacts[] | select(.name == $name) | .id' | head -n 1)"

if [ -z "$artifact_id" ]; then
  echo "Artifact ${ARTIFACT_NAME} not found for run ${run_id}" >&2
  exit 1
fi

zip_path="$(mktemp)"
trap 'rm -f "$zip_path"' EXIT
gh api "repos/${REPO}/actions/artifacts/${artifact_id}/zip" > "$zip_path"

python3 - <<'PY' "$zip_path" "$TARBALL_NAME"
import sys
import zipfile

zip_path = sys.argv[1]
tarball = sys.argv[2]

with zipfile.ZipFile(zip_path) as zf:
    if tarball not in zf.namelist():
        raise SystemExit(f"Missing {tarball} in artifact archive")
    zf.extract(tarball, ".")
PY

echo "$run_sha"
