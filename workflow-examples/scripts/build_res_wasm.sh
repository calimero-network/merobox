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

# `cargo mero build` already embeds each app's ABI as the `calimero_abi_v1`
# section — that is the whole reason core moved to it — so the two apps built
# above need no embed pass here any more. What still does is `kv_store_v2.wasm`
# below: a checked-in blob with no source in either repo, so nothing builds it and
# nothing embeds its schema.
#
# Why any of this matters: calimero-network/core#3286 made an upgrade whose TARGET
# build carries no embedded ABI a hard refusal ("refusing to swap bytecode without
# migration evidence"), and core's decision table needs BOTH sides —
# plan_upgrade() bails with AbiUnavailable{Current} when the running app has no
# ABI either. merod:edge (master) enforces it, the released binary does not, which
# is why group-upgrade-example passed in binary mode and failed in docker mode.
echo "Building mero-abi (embed tool) from $CORE_DIR ..."
(cd "$CORE_DIR" && cargo build -p mero-abi --release)
if [ -n "${CARGO_TARGET_DIR:-}" ]; then
  ABI_TOOL="$CARGO_TARGET_DIR/release/mero-abi"
else
  ABI_TOOL="$CORE_DIR/target/release/mero-abi"
fi
[ -x "$ABI_TOOL" ] || { echo "ERROR: mero-abi not found at $ABI_TOOL" >&2; exit 1; }

KV_SCHEMA="$CORE_DIR/apps/kv-store/res/state-schema.json"
BLOBS_SCHEMA="$CORE_DIR/apps/blobs/res/state-schema.json"
for f in "$KV_SCHEMA" "$BLOBS_SCHEMA"; do
  # `cargo mero build` emits these; a missing one means the build did not do its
  # job, which is worth failing on even though only KV's is embedded below.
  [ -f "$f" ] || { echo "ERROR: state schema missing: $f (cargo mero build should emit it)" >&2; exit 1; }
done


# kv_store_v2.wasm is a checked-in, purpose-built second binary for the SAME
# kv-store state: the upgrade workflows need two distinct blobs to swap between,
# and there is no v2 source in this repo to emit a schema from. Giving it
# kv-store's own schema states the truth — same state_root, same state_version —
# and an equal version on both sides is exactly what makes core resolve the hop
# as UpgradeAction::CodeOnly, no migration edge required.
if [ -f "$RES_DIR/kv_store_v2.wasm" ]; then
  "$ABI_TOOL" embed "$RES_DIR/kv_store_v2.wasm" "$KV_SCHEMA"
fi

echo "Done. kv_store.wasm and blobs.wasm (ABI embedded by cargo mero) in $RES_DIR"
