#!/usr/bin/env bash
# Build the .mpk bundles the workflow examples install, from calimero-network/core.
# The node refuses a raw .wasm on the dev install, so every fixture is a bundle.
#
# Usage: ./workflow-examples/scripts/build_res_bundles.sh
#        CORE_REPO_DIR=/path/to/core ./workflow-examples/scripts/build_res_bundles.sh
#
# CORE_BRANCH must match the merod these run against: an app built from master
# can import a host function a released merod lacks, which dies at instantiation.

set -euo pipefail

CORE_REPO_URL="${CORE_REPO_URL:-https://github.com/calimero-network/core.git}"
CORE_BRANCH="${CORE_BRANCH:-master}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$SCRIPT_DIR/../res" "$SCRIPT_DIR/../.core-repo"
RES_DIR="$(cd "$SCRIPT_DIR/../res" && pwd)"
CORE_REPO_DIR_DEFAULT="$(cd "$SCRIPT_DIR/../.core-repo" && pwd)"

if [ -n "${CORE_REPO_DIR:-}" ]; then
  CORE_DIR="$CORE_REPO_DIR"
else
  CORE_DIR="$CORE_REPO_DIR_DEFAULT"
  if [ ! -d "$CORE_DIR/.git" ]; then
    echo "Cloning $CORE_REPO_URL ($CORE_BRANCH) into $CORE_DIR ..."
    git clone --depth 1 --branch "$CORE_BRANCH" "$CORE_REPO_URL" "$CORE_DIR"
  else
    echo "Updating existing clone at $CORE_DIR ..."
    (cd "$CORE_DIR" && git fetch origin "$CORE_BRANCH" && git checkout "$CORE_BRANCH" && git pull --ff-only)
  fi
fi

echo "Building bundles from $CORE_DIR ..."
rustup target add wasm32-unknown-unknown 2>/dev/null || true

# `cargo mero`, not `cargo build`: the latter emits no ABI, which the node needs
# to introspect an upgrade target.
PATH="$(cd "$CORE_DIR" && ./scripts/setup-cargo-mero.sh):$PATH"
export PATH

# `cd`, not --manifest-path: that puts the output in the workspace root's dist/.
# `--dev` signs with the development key, which a local node accepts.
bundle() { # <app dir> <package id> <app version>
  echo ">>> Bundling $1 ($2 @ $3)"
  (cd "$CORE_DIR/$1" && cargo mero bundle --dev --no-icon --package "$2" --app-version "$3")
  cp "$CORE_DIR/dist/$2-$3.mpk" "$RES_DIR/"
}

bundle apps/kv-store com.calimero.kv-store 1.0.0
bundle apps/blobs com.calimero.blobs 1.0.0

# Its own package, not just its own version: `ApplicationId::for_bundle` hashes
# package and signer, so one package would give both installs a single id.
bundle apps/kv-store com.calimero.kv-store-v2 1.0.0

echo "Done. Bundles in $RES_DIR:"
ls -1 "$RES_DIR"/*.mpk
