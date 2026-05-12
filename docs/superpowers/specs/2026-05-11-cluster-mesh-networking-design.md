# Deterministic cluster networking for multi-node merobox runs

**Issue:** [merobox#231](https://github.com/calimero-network/merobox/issues/231) · **PR:** [merobox#232](https://github.com/calimero-network/merobox/pull/232)
**Date:** 2026-05-11
**Status:** Implemented (PR #232)

## Implementation notes (corrections found during CI)

- **Bootstrap multiaddrs use `/ip4/<container-ip>/...`, not `/dns4/<container>/...`.** merod's
  libp2p `Swarm` is built without a DNS transport (`crates/network/Cargo.toml` has no `"dns"`
  feature; `behaviour.rs::build_swarm` never calls `.with_dns()`), so `/dns4/` multiaddrs
  cannot be dialed. merobox now reads each container's IP on the cluster network (after it
  starts) and wires `/ip4/<ip>/tcp|udp/2428/...`. The dedicated bridge is still created, but
  only for isolation — DNS isn't relied on.
- **`GET /admin-api/peers` returns `{"count": N}`** (a count, not a `{"data":{"peers":[...]}}`
  list). The wait-gate parser (`_peers_count_from_response`) and the pre-existing
  `health.py::extract_peers_count` both only understood the list shape and silently reported
  `0`; both now handle `{"count": N}`.
- merod only feeds `bootstrap.nodes` into Kademlia (`kad.add_address` + `kad.bootstrap()`) —
  it doesn't hold a dedicated persistent connection per bootstrap node. Persistent peer
  connections come from mDNS (direct dial) and rendezvous discovery (direct dial); merobox
  runs no rendezvous server. So the wiring populates Kademlia with dialable sibling addresses
  but, by itself, isn't guaranteed to produce the connected-peer counts the gate checks — in
  practice mDNS on the bridge does connect the peers. (If a deterministic peering without a
  rendezvous server is wanted, that's a core-side change — a "static keep-alive peers" list,
  and/or adding the `dns` transport so `/dns4/` works.)

## Status note (re-scoping, 2026-05-11)

Per [issue #231 comment](https://github.com/calimero-network/merobox/issues/231#issuecomment-4420128766):
the empty gossipsub mesh turned out **not** to be the cross-node flake cause — the mesh is
`{}` for the entire run on every node in every suite (passing PRs and a 1-hour `master`
baseline included); state still propagates via periodic DAG/HashComparison sync over direct
streams; the actual fuzzy flake is a node-side `SyncSessionActor` wedge tracked separately in
`core`. So this work is a **performance/robustness** item (keep the broadcast fast-path warm;
exercise a wider code path; full connectivity at startup), not a CI-green fix. The design
below stands — the comment explicitly still wants "auto-populate `bootstrap.nodes` for
multi-node clusters, and/or a user-defined Docker bridge". The connectivity wait gate is
kept hard-fail, but it checks **connected-peer count**, never gossipsub mesh size (which is
expected to stay empty).

## Problem

When merobox runs a multi-node cluster (and especially under `--e2e-mode`, which `core`'s
`fuzzy-load-test` CI uses), the libp2p gossipsub mesh never forms. Nodes connect over
TCP/QUIC fine (`total_peers` grows) but `Updating mesh, new mesh: {}` is logged for the
entire run and the mesh peer count stays 0. Downstream in `core`, `broadcast()` drops state
deltas when the mesh has 0 peers ([core#2122]), so cross-node propagation falls back on
periodic sync — slower, and it doesn't run event handlers ([core#2139]).

Root cause on the merobox side:

1. `apply_e2e_defaults()` (`merobox/commands/config_utils.py`) wipes `bootstrap.nodes = []`
   and sets `discovery.rendezvous.namespace` — but **no rendezvous server is ever started**
   anywhere in merobox, and rendezvous points are normally reached via bootstrap nodes,
   which were just emptied. mDNS is left on as the only live discovery path.
2. Cluster nodes run on Docker's **default `bridge`** (`run_config["network"]` is only set
   when the auth/Traefik stack is enabled) → no DNS, raw-IP only.
3. `merod init` always uses `--server-host 0.0.0.0` and `discovery.advertise_address` defaults
   to `false`, so identify can advertise the unroutable `0.0.0.0` form.
4. `apply_bootstrap_nodes()` exists and is wired through `run_node`/`run_multiple_nodes`, but
   only fires when a workflow explicitly passes `bootstrap_nodes:` — the fuzzy workflows
   don't.
5. `run_multiple_nodes` returns as soon as containers are up — there is no "mesh formed" gate.

mDNS over `docker0` "mostly works", which is why short e2e tests and single-node ops pass,
but it doesn't reliably build/repair a gossipsub mesh under a sustained 15-minute load test.

(Historical note: the `[0.2.6]` CHANGELOG entry claims merobox uses "rendezvous-based
discovery instead of unreliable mDNS in CI" — the rendezvous side was never wired to a
server, so CI has been mDNS-only the whole time. This spec corrects that line.)

## Goals

- Multi-node clusters form a gossipsub mesh **deterministically**, by using the *production*
  discovery path (static `bootstrap.nodes` peer list, like devnet) instead of the mDNS-only
  crutch.
- Make the fuzzy job's signal **stronger, not weaker**: a `cross_node_*` failure should mean
  "core's data plane is broken", not "mDNS had a bad day". Add a hard-fail precondition check
  so a cluster that *can't* form connectivity fails loudly at startup instead of flakily
  mid-run.
- No behavior change for single-node runs or for clusters that already pass an explicit
  `bootstrap_nodes:` list (those are honored; we only *augment*).

## Non-goals

- Simulating adversarial network conditions (partitions, latency/loss injection, peer churn,
  NAT traversal). All nodes still sit on one flat L2 bridge, directly dialable — same as the
  current default-bridge setup. A deliberate chaos/partition capability is a **separate
  follow-up issue**, not this change.
- Exposing gossipsub mesh peer count via merod's admin API (not available today; the wait
  gate uses connected-peer count as the observable proxy and will pick up a mesh-count
  endpoint if one ever lands).
- Changing `binary_manager.py`'s networking (it isn't containerized — no Docker network
  applies). It already calls `apply_bootstrap_nodes`; auto-wiring sibling peers there is a
  nice-to-have, out of scope unless trivial.

## Activation

The new behavior applies whenever `count > 1` in `run_multiple_nodes` (the Docker path):

| Case | Dedicated Docker network? | Auto-wire sibling bootstrap peers? |
|------|---------------------------|-------------------------------------|
| `count == 1` | no | no (unchanged) |
| `count > 1`, no auth, no explicit `bootstrap_nodes` | yes — `merobox-cluster` bridge | yes — overwrite `bootstrap.nodes` with siblings |
| `count > 1`, no auth, explicit `bootstrap_nodes` given | yes — `merobox-cluster` bridge | yes — **append** siblings to the given list |
| `count > 1`, `auth_service` on | no — nodes already on `calimero_web` (has DNS) | yes — using `calimero_web` DNS names |

A kill-switch env var (`MEROBOX_LEGACY_CLUSTER_NETWORKING=1`, name TBD-in-plan) disables all
of the above and restores the old default-bridge + mDNS-only behavior + non-blocking return.

## Design

### 1. Cluster network lifecycle

Add `DockerManager._ensure_cluster_network()` — mirrors the existing `_ensure_auth_networks()`:

- Idempotent: `client.networks.get("merobox-cluster")`; on `NotFound`,
  `client.networks.create(name="merobox-cluster", driver="bridge")`.
- Returns the network name on success, `None` on failure (caller falls back to default bridge
  + a warning; auto-wiring is then skipped because `/dns4/<container>/...` won't resolve).
- Containers keep their existing `name` (`calimero-node-1`, …) as the DNS hostname on the
  network.
- `run_node` gains a `network: str = None` param; in the run step,
  `run_config["network"] = "calimero_web"` when `auth_service` else `run_config["network"] =
  network` when `network` is set, else unchanged (default bridge).
- Cleanup: leave the `merobox-cluster` network in place on `stop` (same as `calimero_web`
  today — empty bridge networks are harmless). Optionally remove it during `stop --all`/full
  teardown when no `calimero.node` containers remain. (Decided in the plan; default = leave
  it.)

The container's internal P2P port is always `DEFAULT_P2P_PORT` (2428), confirmed from the
`merod ... init --swarm-port {DEFAULT_P2P_PORT}` call — so sibling multiaddrs always use 2428.

### 2. Two-phase startup in `run_multiple_nodes`

Today `run_node` does init → configure → run in one call. For multi-node clusters, split it:

- **Phase A — prepare (parallel):** for each node, do everything `run_node` does *except* the
  final `client.containers.run(...)`: clean up stale containers, run `merod ... init`, apply
  `apply_e2e_defaults` (if `e2e_mode`), then read `[identity].peer_id` from the freshly
  written `config.toml`. Yields `(node_name, peer_id, container_config, run_config)`.
- **Phase B — wire bootstrap peers (sequential, cheap):** with all peer IDs known, for each
  node write `bootstrap.nodes` = (explicit `bootstrap_nodes` if any) + for every *other*
  node: `/dns4/<sibling-container>/tcp/2428/p2p/<sibling-peer-id>` **and**
  `/dns4/<sibling-container>/udp/2428/quic-v1/p2p/<sibling-peer-id>`. Reuse/extend
  `apply_bootstrap_nodes`. Leave `discovery.mdns = true` as a fallback; leave the rendezvous
  config untouched. Fix file ownership first if needed (init container ran as root) —
  idempotent with what `run_node` already does in e2e mode.
- **Phase C — run (parallel):** `client.containers.run(...)` per node, then the existing
  post-start status check.

Refactor: extract the shared body of `run_node` so the single-node path and the
prepare/launch multi-node path don't duplicate. This is the bulk of the diff and lands mostly
in `manager.py`. If extraction proves too invasive in one pass, the fallback is the
restart-after-launch approach (init → run → rewrite config → `container.restart()`) — works
but churns every container ~`NODE_STARTUP_DELAY` after start; prefer two-phase.

`apply_bootstrap_nodes`/the multiaddr builder must:
- exclude the node itself,
- skip any sibling whose peer ID couldn't be read (and bail to mDNS-only with a warning if
  fewer than 2 peer IDs were obtained),
- append to (not clobber) an explicit `bootstrap_nodes` list when one was passed.

### 3. Hard-fail "cluster connected" wait gate

Add `DockerManager.wait_for_cluster_peers(node_names, expected_peers, timeout)`:

- Polls each node's `GET /admin-api/peers` (the endpoint `merobox/commands/health.py`
  already uses) until **every** node reports `>= expected_peers` connected peers, where
  `expected_peers = count - 1`.
- On timeout: log which nodes are short and how many peers each has, then **return `False`**.
- `run_multiple_nodes` calls this after Phase C for `count > 1` clusters and folds the result
  into its return value, so a cluster that never reaches full connectivity makes the CI step
  **fail** (instead of the current "containers up → return True → flake 12 minutes later").
- Parameters: `timeout` defaults to a sane value (~60s, tuned in the plan); per-poll interval
  small (~2s). The `MEROBOX_LEGACY_CLUSTER_NETWORKING` kill-switch also makes this
  non-blocking (warn-only, always returns `True`) for anyone who needs the old behavior.
- Rationale for the proxy: gossipsub mesh peer count is internal libp2p state, not exposed via
  admin-api. Connected-peer count is the best observable signal; with siblings configured as
  bootstrap peers *before* `merod run` subscribes to topics, the gossipsub `JOIN` has peers
  to insert, so full connectivity is a strong predictor of a non-empty mesh. If/when merod
  exposes a mesh-count endpoint, it slots into this same gate.

### 4. CHANGELOG correction

- Fix the `[0.2.6]` line that claims "rendezvous-based discovery instead of unreliable mDNS
  in CI" — note it was never wired to a rendezvous server.
- Add an `[Unreleased] / Fixed` entry describing the new deterministic cluster networking and
  referencing #231.

## Testing

merobox uses pytest (`merobox/tests/unit/...`). Add:

- **`config_utils`**: `read_peer_id()` — extracts `identity.peer_id` from a sample TOML;
  returns `None` on missing key / missing file. Multiaddr builder — correct
  `/dns4/.../tcp/2428/p2p/<pid>` + quic forms, self-excluded, append-vs-overwrite with an
  explicit `bootstrap_nodes` list, bail-to-mDNS when < 2 peer IDs.
- **`manager`**: `_ensure_cluster_network()` idempotency (mock `client.networks` — `get`
  succeeds → no `create`; `get` raises `NotFound` → `create` called once; `create` raises →
  returns `None`). Phase-B wiring — given mocked peer IDs and node configs, every node's
  config gets a parseable, self-excluded `bootstrap.nodes`. `run_node(network=...)` sets
  `run_config["network"]` correctly (and `auth_service` still wins).
- **`wait_for_cluster_peers`**: mock `/admin-api/peers` — returns `True` when every node
  reports `>= expected_peers`; returns `False` on timeout; reports which nodes were short.
- Keep tests Docker-free (mock `docker.from_env` / `client`), consistent with existing
  `test_docker_manager.py`.

## Follow-up (separate issue, not this change)

A deliberate chaos/partition step for adversarial gossipsub fuzzing: latency/loss injection
(`tc netem` in a sidecar or via a network plugin), peer churn (stop/start nodes mid-run),
partition healing. So "the happy-path topology forms reliably" (this change) and "it survives
bad networks" (future) are both covered, rather than the latter being faked by a
misconfigured bridge.

## Open decisions (resolve in the implementation plan)

- Final name of the kill-switch env var.
- Whether `stop --all` removes the `merobox-cluster` network (default: no).
- Exact `wait_for_cluster_peers` timeout/interval defaults.
- Whether to also auto-wire sibling bootstrap peers in `binary_manager.run_multiple_nodes`
  (no Docker network there; would use `127.0.0.1:<p2p_port>` multiaddrs) — include only if
  cheap.

[core#2122]: https://github.com/calimero-network/core/issues/2122
[core#2139]: https://github.com/calimero-network/core/issues/2139
