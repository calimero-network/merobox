#!/usr/bin/env bash
# Build the .mpk bundles the workflow examples install, from calimero-network/core.
#
# The node refuses a raw .wasm on the dev install ("not a signed application
# bundle"), so every fixture here is a `cargo mero bundle` output rather than the
# bare wasm this script used to copy.
#
# Usage:
#   From merobox repo root:  ./workflow-examples/scripts/build_res_bundles.sh
#   Or:                      CORE_REPO_DIR=/path/to/core ./workflow-examples/scripts/build_res_bundles.sh
#
# Set CORE_REPO_DIR to use an existing core clone; otherwise we clone into
# workflow-examples/.core-repo.
#
# CORE_BRANCH should match the merod you intend to run these bundles against. It
# defaults to master, which is right for merod:edge - but an app built from master
# can import a host function a RELEASED merod does not have (`account_id` since
# calimero-network/core#3320), and that fails at instantiation with
# Link(Import("env", "account_id", UnknownImport)) rather than anything that names
# a version skew. CI pins CORE_BRANCH to the release tag it downloaded merod from
# for exactly this reason; do the same locally if you are testing a release.

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

# `cargo mero`, not the per-app `build.sh` scripts: core removed those in
# calimero-network/core#3308, and a bare `cargo build` emits no ABI, which the
# node needs to introspect an upgrade target.
PATH="$(cd "$CORE_DIR" && ./scripts/setup-cargo-mero.sh):$PATH"
export PATH

# `cd`, not --manifest-path: without it the output lands in the workspace root's
# dist/, which is where the copy below reads from. `--dev` signs with the
# well-known development key, which a local node accepts and the registry refuses.
bundle() { # <app dir> <package id> <app version>
  echo ">>> Bundling $1 ($2 @ $3)"
  (cd "$CORE_DIR/$1" && cargo mero bundle --dev --no-icon --package "$2" --app-version "$3")
  cp "$CORE_DIR/dist/$2-$3.mpk" "$RES_DIR/"
}

bundle apps/kv-store com.calimero.kv-store 1.0.0
bundle apps/blobs com.calimero.blobs 1.0.0

# The upgrade workflows install two applications and compare their ids, so the
# second needs its OWN package: `ApplicationId::for_bundle` hashes package and
# signer, not version, so a shared package would hand both installs one id and
# the comparison would pass while testing nothing. Same source as v1 on purpose -
# what those workflows exercise is the upgrade machinery, not a behaviour change.
bundle apps/kv-store com.calimero.kv-store-v2 1.0.0

echo "Done. Bundles in $RES_DIR:"
ls -1 "$RES_DIR"/*.mpk
