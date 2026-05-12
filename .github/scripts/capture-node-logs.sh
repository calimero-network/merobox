#!/usr/bin/env bash
#
# Stream merobox-managed node container logs to disk in the background, so they
# survive `merobox stop`/`nuke` (which removes the containers — a post-run
# `docker logs` would find nothing). Mirrors the live-follow pattern used by
# core's fuzzy-load-test workflow.
#
# Usage (typically: one `start` step, then the test step, then an
# `if: always()` `stop` step, then an upload-artifact step on $LOGDIR):
#
#   capture-node-logs.sh start <logdir> <state-file>
#   ... run merobox workflow / tests ...
#   capture-node-logs.sh stop  <state-file>
#
# `state-file` is an opaque handle written by `start` and consumed by `stop`.
set -euo pipefail

cmd="${1:-}"

case "$cmd" in
  start)
    logdir="${2:?usage: capture-node-logs.sh start <logdir> <state-file>}"
    state_file="${3:?usage: capture-node-logs.sh start <logdir> <state-file>}"
    mkdir -p "$logdir"
    state_dir="$(mktemp -d)"
    : > "$state_dir/follower-pids"

    (
      # Re-follow a container if its name reappears (merobox retries tear
      # down and recreate containers with the same name); append so the
      # earlier attempt's output is kept. Record each follower PID so `stop`
      # can target exactly these processes (no broad `pkill -f`).
      declare -A pids=()
      while [ ! -f "$state_dir/stop" ]; do
        for c in $(docker ps --filter "label=calimero.node=true" \
                              --format '{{.Names}}' 2>/dev/null \
                   | grep -v -- '-init$' || true); do
          if [ -z "${pids[$c]:-}" ] || ! kill -0 "${pids[$c]}" 2>/dev/null; then
            echo "[node-log-capture] following $c -> $logdir/$c.log"
            docker logs -f --timestamps "$c" >> "$logdir/$c.log" 2>&1 &
            pids[$c]=$!
            echo "$!" >> "$state_dir/follower-pids"
          fi
        done
        sleep 1
      done
    ) >/dev/null 2>&1 &
    watcher_pid=$!

    printf '%s\n%s\n' "$watcher_pid" "$state_dir" > "$state_file"
    echo "[node-log-capture] started (watcher pid=$watcher_pid; logs -> $logdir)"
    ;;

  stop)
    state_file="${2:?usage: capture-node-logs.sh stop <state-file>}"
    if [ ! -f "$state_file" ]; then
      echo "[node-log-capture] no state file ($state_file); nothing to stop"
      exit 0
    fi
    watcher_pid="$(sed -n 1p "$state_file" || true)"
    state_dir="$(sed -n 2p "$state_file" || true)"
    [ -n "$state_dir" ] && touch "$state_dir/stop" || true
    # Let the `docker logs -f` followers flush their tail before we stop them.
    sleep 2
    [ -n "$watcher_pid" ] && kill "$watcher_pid" 2>/dev/null || true
    if [ -n "$state_dir" ] && [ -f "$state_dir/follower-pids" ]; then
      while read -r pid; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
      done < "$state_dir/follower-pids"
    fi
    echo "[node-log-capture] stopped"
    ;;

  *)
    echo "usage: capture-node-logs.sh start <logdir> <state-file> | stop <state-file>" >&2
    exit 2
    ;;
esac
