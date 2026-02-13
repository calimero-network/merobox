#!/usr/bin/env bash
# Build kv_store.wasm, blobs.wasm, and blockchain.wasm from calimero-network/core and copy to workflow-examples/res/.
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
# Build blockchain app (output: target/wasm32-unknown-unknown/app-release/blockchain.wasm)
(cd "$CORE_DIR" && cargo build -p blockchain --target wasm32-unknown-unknown --profile app-release)
mkdir -p "$CORE_DIR/apps/demo-blockchain-integrations/res"
cp "$CORE_DIR/target/wasm32-unknown-unknown/app-release/blockchain.wasm" "$CORE_DIR/apps/demo-blockchain-integrations/res/"

mkdir -p "$RES_DIR"
cp "$CORE_DIR/apps/kv-store/res/kv_store.wasm" "$RES_DIR/"
cp "$CORE_DIR/apps/blobs/res/blobs.wasm" "$RES_DIR/"
cp "$CORE_DIR/apps/demo-blockchain-integrations/res/blockchain.wasm" "$RES_DIR/"
echo "Done. Copied kv_store.wasm, blobs.wasm, and blockchain.wasm to $RES_DIR"
