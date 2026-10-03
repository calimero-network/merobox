#!/bin/sh
# Run one subcommand of relay-account/relay-account.mjs (the nodeless-account
# half of relay-founded-namespace-ha.yml), installing its pinned mero-js on
# first use. Needs node >= 20 and npm.
set -eu

DIR="$(cd "$(dirname "$0")/relay-account" && pwd)"

if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: node is not on PATH (needed for the mero-js account half)" >&2
  exit 1
fi

if [ ! -d "$DIR/node_modules/@calimero-network/mero-js" ]; then
  echo "installing mero-js into $DIR ..."
  (cd "$DIR" && npm install --no-audit --no-fund --silent) >&2
fi

exec node "$DIR/relay-account.mjs" "$@"
