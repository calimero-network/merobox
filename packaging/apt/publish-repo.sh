#!/bin/bash
# Publish .deb packages to APT repository using reprepro
# Usage: ./publish-repo.sh <repo-dir> <deb-file> [<deb-file2> ...]

set -euo pipefail

REPO_DIR="${1:?Repository directory required}"
shift

if [ $# -eq 0 ]; then
    echo "Error: At least one .deb file required"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${APT_GPG_FINGERPRINT:-}" ]; then
    echo "Error: APT_GPG_FINGERPRINT environment variable not set"
    exit 1
fi

mkdir -p "${REPO_DIR}/conf"
envsubst < "${SCRIPT_DIR}/reprepro/conf/distributions" > "${REPO_DIR}/conf/distributions"
cp "${SCRIPT_DIR}/reprepro/conf/options" "${REPO_DIR}/conf/options"

for DEB_FILE in "$@"; do
    if [ ! -f "${DEB_FILE}" ]; then
        echo "Error: File not found: ${DEB_FILE}"
        exit 1
    fi
    reprepro -b "${REPO_DIR}" includedeb stable "${DEB_FILE}"
done

reprepro -b "${REPO_DIR}" export stable

# Verify required release files exist
for FILE in Release Release.gpg InRelease; do
    if [ ! -f "${REPO_DIR}/dists/stable/${FILE}" ]; then
        echo "Error: Missing ${REPO_DIR}/dists/stable/${FILE}"
        exit 1
    fi
done

rm -rf "${REPO_DIR}/conf" "${REPO_DIR}/db"
