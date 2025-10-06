#!/usr/bin/env sh
set -eu

echo "[script] Running echo-exported-value.sh"

if [ $# -ge 1 ]; then
  echo "Exported value is: $1"
else
  echo "Exported value is: <none>"
fi