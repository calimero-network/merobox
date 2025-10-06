#!/usr/bin/env sh
set -euo pipefail

echo "[script] Running echo-exported-value.sh"

if [ $# -ge 1 ]; then
  echo "Exported value is: $1"
else
  echo "Exported value is: <none>"
fi