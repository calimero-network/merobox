#!/usr/bin/env bash
# Serves the workflow-example bundles over HTTP in the layout the app downloader
# expects, so a node resolves `{package, version}` the way a real deployment does.
#
# Usage:
#   ./workflow-examples/scripts/fixture-registry.sh [--stamp IMAGE] [bundle dir]
#   ./workflow-examples/scripts/fixture-registry.sh --assert WORKFLOW
#
# Prints the base URL to serve as CALIMERO_REGISTRY_URL. `--stamp` also bakes it
# into IMAGE, which is the only route a containerised node has (see below).
set -euo pipefail

readonly CONTAINER=fixture-registry
readonly IMAGE=nginx:alpine

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stamp_image=""
if [ "${1:-}" = --stamp ]; then
  stamp_image="${2:?--stamp needs an image}"
  shift 2
fi

# A coordinate install that never reached the fixture resolved its bytecode
# somewhere else, so a green run would be hiding a broken registry.
if [ "${1:-}" = --assert ]; then
  workflow="${2:?--assert needs a workflow file}"
  python3 - "$workflow" <<'PY' || exit 0
import sys, yaml
def flat(steps):
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        yield s
        yield from flat(s.get("steps"))
        for g in s.get("groups") or []:
            if isinstance(g, dict):
                yield from flat(g.get("steps"))
d = yaml.safe_load(open(sys.argv[1])) or {}
uses = any(s.get("type") == "install_application" and s.get("package") for s in flat(d.get("steps")))
sys.exit(0 if uses else 1)
PY
  if ! docker logs "$CONTAINER" 2>&1 | grep -q 'GET /artifacts/'; then
    echo "::error::$workflow installs by coordinates but fetched nothing from the fixture registry"
    docker logs "$CONTAINER" 2>&1 | tail -50 || true
    exit 1
  fi
  exit 0
fi

bundle_dir="${1:-$SCRIPT_DIR/../res}"
root=$PWD/fixture-registry
port=8080

# `{package}-{version}.mpk`, split on the dash that starts the version; the
# package half contains dashes of its own.
stage() {
  local bundles mpk base dest
  shopt -s nullglob
  bundles=("$bundle_dir"/*.mpk)
  # res/ is gitignored and ships empty, so an unbuilt tree lands here first.
  if [ ${#bundles[@]} -eq 0 ]; then
    echo "no .mpk bundles in $bundle_dir - run build_res_bundles.sh first" >&2
    return 1
  fi
  rm -rf "$root"
  for mpk in "${bundles[@]}"; do
    base=$(basename "$mpk" .mpk)
    if [[ ! $base =~ ^(.+)-([0-9]+\.[0-9]+\.[0-9]+.*)$ ]]; then
      echo "bundle name is not {package}-{version}.mpk: $mpk" >&2
      return 1
    fi
    dest="$root/artifacts/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    mkdir -p "$dest"
    # Copied, never repacked: a rewritten bundle changes its bytecode id.
    cp "$mpk" "$dest/$base.mpk"
  done
}

stage

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" -p "$port:80" \
  -v "$root:/usr/share/nginx/html:ro" "$IMAGE" >/dev/null

# Published on the host and addressed by the docker0 gateway: that address is
# the host itself, so it resolves from every bridge network and from a merod
# running natively on the host. Loopback would only serve the latter.
gateway=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
[ -n "$gateway" ] || { echo "no gateway on the default bridge" >&2; exit 1; }
url="http://$gateway:$port/"

# merobox has no per-node env key, so a container's registry has to travel in
# the image it boots from; a native merod inherits the caller's environment.
if [ -n "$stamp_image" ]; then
  printf 'FROM %s\nENV CALIMERO_REGISTRY_MODE=http\nENV CALIMERO_REGISTRY_URL=%s\n' \
    "$stamp_image" "$url" | docker build -q -t "$stamp_image" - >/dev/null
fi

echo "$url"
