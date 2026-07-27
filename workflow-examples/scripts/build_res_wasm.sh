#!/usr/bin/env bash
# Build kv_store.wasm and blobs.wasm from calimero-network/core and copy to workflow-examples/res/.
# Requires: git, rustup, cargo (Rust toolchain). Optional: wasm-opt for smaller binaries.
#
# Usage:
#   From merobox repo root:  ./workflow-examples/scripts/build_res_wasm.sh
#   Or:                     CORE_REPO_DIR=/path/to/core ./workflow-examples/scripts/build_res_wasm.sh
#
# Set CORE_REPO_DIR to use an existing core clone; otherwise we clone into workflow-examples/.core-repo.

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
  if [ ! -d "$CORE_DIR/.git" ]; then
    echo "Cloning $CORE_REPO_URL ($CORE_BRANCH) into $CORE_DIR ..."
    git clone --depth 1 --branch "$CORE_BRANCH" "$CORE_REPO_URL" "$CORE_DIR"
  else
    echo "Updating existing clone at $CORE_DIR ..."
    (cd "$CORE_DIR" && git fetch origin "$CORE_BRANCH" && git checkout "$CORE_BRANCH" && git pull --ff-only)
  fi
fi

echo "Building WASM apps from $CORE_DIR ..."
rustup target add wasm32-unknown-unknown 2>/dev/null || true

# Build kv-store (output: apps/kv-store/res/kv_store.wasm)
(cd "$CORE_DIR/apps/kv-store" && ./build.sh)
# Build blobs (output: apps/blobs/res/blobs.wasm)
(cd "$CORE_DIR/apps/blobs" && ./build.sh)

mkdir -p "$RES_DIR"
cp "$CORE_DIR/apps/kv-store/res/kv_store.wasm" "$RES_DIR/"
cp "$CORE_DIR/apps/blobs/res/blobs.wasm" "$RES_DIR/"

# Embed each app's state schema as the wasm's `calimero_abi_v1` section. This has
# to happen AFTER build.sh, because its wasm-opt pass strips custom sections.
#
# calimero-network/core#3286 made an upgrade whose TARGET build carries no
# embedded ABI a hard refusal ("refusing to swap bytecode without migration
# evidence"), and core's decision table needs BOTH sides: plan_upgrade() bails
# with AbiUnavailable{Current} when the running app has no ABI either. merod:edge
# (master) enforces it, the released binary does not — which is why
# group-upgrade-example passed in binary mode and failed in docker mode.
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
  [ -f "$f" ] || { echo "ERROR: state schema missing: $f (build.rs should emit it)" >&2; exit 1; }
done

echo "Embedding state schemas ..."
"$ABI_TOOL" embed "$RES_DIR/kv_store.wasm" "$KV_SCHEMA"
"$ABI_TOOL" embed "$RES_DIR/blobs.wasm" "$BLOBS_SCHEMA"

# kv_store_v2.wasm is a checked-in, purpose-built second binary for the SAME
# kv-store state: the upgrade workflows need two distinct blobs to swap between,
# and there is no v2 source in this repo to emit a schema from. Giving it
# kv-store's own schema states the truth — same state_root, same state_version —
# and an equal version on both sides is exactly what makes core resolve the hop
# as UpgradeAction::CodeOnly, no migration edge required.
if [ -f "$RES_DIR/kv_store_v2.wasm" ]; then
  "$ABI_TOOL" embed "$RES_DIR/kv_store_v2.wasm" "$KV_SCHEMA"
fi

echo "Done. Copied + ABI-embedded kv_store.wasm and blobs.wasm in $RES_DIR"
