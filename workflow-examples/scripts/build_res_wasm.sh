#!/usr/bin/env bash
# Build kv_store.wasm and blobs.wasm from calimero-network/core and copy to workflow-examples/res/.
# Requires: git, rustup, cargo (Rust toolchain). Optional: wasm-opt for smaller binaries.
#
# Usage:
#   From merobox repo root:  ./workflow-examples/scripts/build_res_wasm.sh
#   Or:                     CORE_REPO_DIR=/path/to/core ./workflow-examples/scripts/build_res_wasm.sh
#
# Set CORE_REPO_DIR to use an existing core clone; otherwise we clone into workflow-examples/.core-repo.
#
# CORE_BRANCH should match the merod you intend to run these wasms against. It
# defaults to master, which is right for merod:edge — but an app built from master
# can import a host function a RELEASED merod does not have (`account_id` since
# calimero-network/core#3320), and that fails at instantiation with
# Link(Import("env", "account_id", UnknownImport)) rather than anything that names
# a version skew. CI pins CORE_BRANCH to the release tag it downloaded merod from
# for exactly this reason; do the same locally if you are testing a release.

set -e

CORE_REPO_URL="${CORE_REPO_URL:-https://github.com/calimero-network/core.git}"
CORE_BRANCH="${CORE_BRANCH:-master}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$SCRIPT_DIR/../res" "$SCRIPT_DIR/../.core-repo"
RES_DIR="$(cd "$SCRIPT_DIR/../res" && pwd)"
CORE_REPO_DIR_DEFAULT="$(cd "$SCRIPT_DIR/../.core-repo" && pwd)"

if [ -n "$CORE_REPO_DIR" ]; then
  CORE_DIR="$CORE_REPO_DIR"
else
  CORE_DIR="$CORE_REPO_DIR_DEFAULT"
  # `fetch` rather than `clone --branch`, so CORE_BRANCH may be a commit SHA as
  # well as a branch or tag. `--branch` only takes refs, and a caller pinning to
  # the exact revision an image was built from has a SHA, not a ref — which is
  # the only way to build an app against the node it will actually run with.
  # GitHub serves reachable SHAs to `fetch`, so one path covers all three.
  if [ ! -d "$CORE_DIR/.git" ]; then
    echo "Fetching $CORE_REPO_URL ($CORE_BRANCH) into $CORE_DIR ..."
    git init -q "$CORE_DIR"
    git -C "$CORE_DIR" remote add origin "$CORE_REPO_URL"
  else
    echo "Updating existing clone at $CORE_DIR ..."
  fi
  git -C "$CORE_DIR" fetch --depth 1 origin "$CORE_BRANCH"
  # FETCH_HEAD, not "$CORE_BRANCH": a shallow fetch of a SHA creates no local
  # branch to check out, and this works identically for refs.
  git -C "$CORE_DIR" checkout -q --detach FETCH_HEAD
  echo "Core is at $(git -C "$CORE_DIR" rev-parse HEAD)"
fi

echo "Building WASM apps from $CORE_DIR ..."
rustup target add wasm32-unknown-unknown 2>/dev/null || true

# `cargo mero build`, not the per-app `build.sh` scripts: core removed those in
# calimero-network/core#3308 ("build all apps through cargo mero"), which broke
# this script for every merobox PR from the moment it landed, since we always
# clone core's CURRENT master. A bare `cargo build` is not a substitute — it
# compiles the same code but emits no ABI and embeds nothing, so the node cannot
# introspect the result.
PATH="$(cd "$CORE_DIR" && ./scripts/setup-cargo-mero.sh):$PATH"
export PATH

# Outputs: apps/<app>/res/<app_name>.wasm, with res/abi.json and
# res/state-schema.json alongside.
(cd "$CORE_DIR" && cargo mero build --manifest-path apps/kv-store/Cargo.toml)
(cd "$CORE_DIR" && cargo mero build --manifest-path apps/blobs/Cargo.toml)

mkdir -p "$RES_DIR"
cp "$CORE_DIR/apps/kv-store/res/kv_store.wasm" "$RES_DIR/"
cp "$CORE_DIR/apps/blobs/res/blobs.wasm" "$RES_DIR/"

# Signed bundles, which is what a node installs since core#3652 made
# distribution registry-only: `install_application` now rejects a bare wasm
# with "not a signed application bundle". The `.wasm` above is still emitted —
# it carries the embedded ABI the schema steps below patch, and nothing reads
# the bundle for that.
#
# `--dev` signs with the well-known dev key deliberately. The seed is
# deterministic, so `ApplicationId = hash(package, signer)` is stable across
# rebuilds, which is what makes a bundle usable as a fixture at all. The
# registry refuses dev-signed bundles, so these cannot escape into real
# distribution.
#
# `--output` because the default name is `<package>-<version>.mpk`, and a
# workflow should not have to know an app's version to name its own fixture.
(cd "$CORE_DIR" && cargo mero bundle --dev --no-icon \
  --manifest-path apps/kv-store/Cargo.toml --output "$RES_DIR/kv_store.mpk")
(cd "$CORE_DIR" && cargo mero bundle --dev --no-icon \
  --manifest-path apps/blobs/Cargo.toml --output "$RES_DIR/blobs.mpk")

# The upgrade target. `core/apps/kv-store-v2`, which the committed
# `kv_store_v2.wasm` was copied from, no longer exists — so the v2 side is
# built from the same source under a different package id. `--package` exists
# for exactly this ("a migration-target bundle under a distinct identity"), and
# a distinct id is what the upgrade path needs: the server refuses a same-app
# upgrade that carries no migration method, which is why two binaries were
# needed in the first place.
(cd "$CORE_DIR" && cargo mero bundle --dev --no-icon \
  --package com.calimero.kv-store-v2 \
  --manifest-path apps/kv-store/Cargo.toml --output "$RES_DIR/kv_store_v2.mpk")

# No manual ABI embed pass any more. `cargo mero build` and `cargo mero bundle`
# both embed the `calimero_abi_v1` section themselves, and the one artifact that
# needed patching by hand — the checked-in `kv_store_v2.wasm`, a blob with no
# source in either repo — is gone: v2 is now built from kv-store above. That
# also removes the reason this script built `mero-abi` at all.
#
# It still matters that both sides carry an ABI: calimero-network/core#3286 made
# an upgrade whose target build has none a hard refusal ("refusing to swap
# bytecode without migration evidence"), and `plan_upgrade()` bails with
# `AbiUnavailable{Current}` when the running app has none either. Building both
# sides from the same source satisfies that by construction rather than by a
# patch step that could silently be skipped.

echo "Done. kv_store/blobs .wasm (ABI embedded) and .mpk bundles in $RES_DIR"
