# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.35] - 2026-06-04

### Added

- New `partition_peers` / `heal_peers` workflow steps: a **surgical peer-pair
  network partition** that drops only container-to-container (libp2p) traffic
  between a node and the listed peers, while leaving the node's published-port
  RPC reachable. Unlike `disconnect_node` (which detaches the container from
  its bridge and so also severs its RPC), this lets a workflow issue `call`s to
  a node *while* it is partitioned from its peers — required for tests that
  must drive both sides of a split (e.g. two nodes concurrently rotating a
  SharedStorage writer set). Implemented as symmetric DROP rules in the host's
  `DOCKER-USER` iptables chain matched on the containers' bridge IPs; a CI /
  Linux primitive (needs iptables + passwordless sudo). `heal_peers` removes
  the rules (same args; idempotent). Closes calimero-network/merobox#278.
  Behavioral coverage in `workflow-fault-injection-partition-peers-example.yml`
  (Docker matrix): proves RPC stays up on the isolated node, the partition
  actually blocks delivery, and heal restores it.

## [0.6.34] - 2026-06-03

### Added

- Container logs are now persisted to `data/container-logs/<name>.log` on every
  `bootstrap run`, regardless of the `stop_all_nodes` setting. Previously logs
  were only captured on the stop path, so workflows that leave nodes running
  (`stop_all_nodes: false`, the default) — including Calimero core's parallel
  e2e jobs — produced no collectable log artifacts, and failures had to be
  diagnosed via an external bash docker-logs workaround. A one-shot dump now
  runs in both the leave-running success branch and the failure-exit path
  (before any stop/teardown), so success and failure runs both yield artifacts.
  Closes calimero-network/merobox#207.

## [0.6.33] - 2026-06-03

### Added

- First-class **auth support in workflows**, so authenticated (and negative)
  e2e tests can be written declaratively against a node's embedded auth router
  (`merod --auth-mode embedded`, no separate `mero-auth` container needed). The
  embedded router auto-creates a root key with `["admin"]` on the first
  `POST /auth/token`, so the first login doubles as setup. New step types:
  - `login` — bootstraps/authenticates against a node and **seeds the on-disk
    token cache** (`~/.merobox/auth_cache/{node}.json`) so every downstream
    `call`/`execute`/`ws_connect` on that node is authenticated automatically.
    Exports `access_token`/`refresh_token` via `outputs`. Set
    `expected_failure: true` to assert that bad credentials are rejected.
  - `refresh` — exercises `POST /auth/refresh`: swaps the cached refresh token
    for a fresh access token and re-seeds the cache.
  - `ws_connect` / `ws_subscribe` — opens a WebSocket against the node's `/ws`
    endpoint with the JWT on the `?token=<jwt>` query param (WS clients can't
    set headers). With a valid cached token the connect must succeed; with
    `unauthenticated: true` + `expected_failure: true` it asserts the upgrade
    handshake is rejected. Mirrors `core/scripts/test-websocket-auth.sh`.
- Negative-test toggle on the `call`/`execute` step: `unauthenticated: true`
  forces a no-token request so a protected endpoint rejects it; pair with the
  existing `expected_failure: true` to assert the 401.
- `expected_failure` on the auth steps is strict: it only passes on a genuine
  auth rejection (a credential/refresh `AuthenticationError`, or a WebSocket
  upgrade returning HTTP 401/403). Connectivity faults — unreachable node,
  connection refused/reset, timeouts, or a non-auth handshake status — fail the
  step rather than green-lighting a meaningless assert. A mis-asserted negative
  `login`/`refresh` (auth unexpectedly succeeds) also no longer seeds the token
  cache, so it can't leave stale credentials behind.
- `auth_mode: embedded` workflows no longer require `--auth-username` /
  `--auth-password`. When omitted, the workflow drives auth declaratively via
  `login` steps; when provided (both together), merobox still auto-authenticates
  every node up front as before. Supplying only one of the pair is now a
  validation error.
- Example workflows under `workflow-examples/`:
  `workflow-embedded-auth-example.yml` (bootstrap/login, authenticated calls,
  the unauthenticated negative assert, WebSocket auth, and the refresh flow) and
  the focused `workflow-websocket-auth-example.yml` (valid token connects, no
  token / bad token rejected).

## [0.6.32] - 2026-05-30

### Added

- `get_cascade_status` workflow step. Reads per-descendant cascade
  migration status across a namespace subtree via the
  `get_cascade_status` RPC (calimero-network/core#2524) and rolls the
  per-group entries up into `total` / `completed` / `pending` / `failed`
  counts plus an `all_completed` flag. The summary is stored under
  `cascade_status_{node}`; its fields — and the raw per-group `groups`
  list — are addressable from an `outputs:` block. Takes `node` and
  `namespace_id`.
- `assert_cascade_complete` workflow step. Polls `get_cascade_status`
  every `poll_interval` seconds (default `2.0`) until every group in the
  namespace subtree has migrated (`all_completed`) or `timeout_seconds`
  (default `30`) elapses. A descendant entering the `failed` status
  aborts the wait immediately. Saves workflow authors from hand-rolling
  a `wait`-loop around `get_cascade_status`. Takes `node`,
  `namespace_id`, and optional `timeout_seconds` / `poll_interval`.
  Together with `cascade_namespace_application` (shipped in 0.6.22) this
  closes calimero-network/merobox#255.

### Changed

- Bumped minimum `calimero-client-py` to `0.6.17`. The
  `get_cascade_status(namespace_id)` binding it adds
  (calimero-network/calimero-client-py#59) backs both new steps; both
  carry a `>= 0.6.17` pre-flight guard for clearer errors on older pins.

## [0.6.31] - 2026-05-30

### Changed

- `workflow-examples/workflow-cascade-namespace-example.yml` — dropped
  the two `upgrade_group(target=app_v1)` alignment workarounds (one on
  the namespace root, one on the subgroup). They existed to paper over
  the random / zero `app_key` defaults at namespace + subgroup
  creation, fixed at source in calimero-network/core#2507. The
  non-cascade `upgrade_group` on the namespace root was also actively
  harmful — trips `validate_upgrade`'s "no contexts to upgrade" check
  because the namespace root holds no contexts (contexts live in the
  subgroup), aborting the workflow before reaching the cascade.
- Bumped `calimero-client-py` pin from `>=0.6.15` to `>=0.6.16`.
  0.6.16 picks up the new `app_key` field on `SignedGroupOpenInvitation`
  (added in calimero-network/core#2507 and exposed through
  calimero-network/calimero-client-py#56). Older client-py versions
  silently dropped the unknown field during the JSON-RPC
  `join_namespace` deserialize, causing joiners' `GroupMeta.app_key` to
  seed to `[0u8; 32]` and any subsequent `CascadeTargetApplicationSet`
  op to silently skip the joiner's subtree.
- CI: excluded `cascade-namespace-example` from the **binary** test
  matrix. Binary mode pulls merod from the latest core *release*, which
  lags master; the cascade predicate only matches once merod derives
  `app_key = blob_id(bytecode)` at group creation
  (calimero-network/core#2507, merged after `0.10.1-rc.47`). Until a
  release `>= 0.10.1-rc.48` ships #2507, the released merod leaves
  `app_key = [0u8; 32]` and the cascade matches nothing — so the binary
  job would fail once the alignment workaround above is dropped. Docker
  mode runs against `merod:edge` (master), which has the fix, so cascade
  coverage is retained there. Re-add to the binary matrix once the
  pinned release carries #2507.

## [0.6.30] - 2026-05-30

### Added

- Console verbosity control for `merobox bootstrap run`, so CI logs are
  readable on the happy path. A `vprint(msg, level=...)` helper in
  `commands/utils.py` gates output through a single process-wide level
  (`quiet` / `normal` / `verbose`), set in priority order by the new
  `-q/--quiet` and existing `-v/--verbose` flags, then the
  `MEROBOX_LOG_LEVEL` env var (useful for CI without touching workflow
  YAML), then the `normal` default. This is merobox's own console
  verbosity — distinct from `--log-level`, which sets the merod node's
  RUST_LOG. By default `wait_for_sync` now emits its banner plus a single
  final success/failure summary and suppresses the per-attempt
  "not converged yet" blocks; `--verbose` (or `MEROBOX_LOG_LEVEL=verbose`)
  restores the full per-attempt detail, and failures always dump the full
  per-target/per-node hash state regardless of level. The previously
  unconditional `run_workflow`/`WorkflowExecutor` debug lines are now
  verbose-only. The same gate is applied to the dominant happy-path log
  noise: the `execute`/`call` step's per-call JSON-RPC response dump and
  resolved-values debug, the `repeat` step's per-iteration banners and
  custom-export confirmations, and the export-machinery's "validated" /
  "no outputs configured" / "Exporting variables" / per-field "✓ name =
  value" lines are all now `verbose`-only. At the `normal` default a
  100-iteration benchmark drops from tens of thousands of log lines to the
  per-block banner plus the final timing summary; genuine warnings and
  errors (missing export field, failed call, malformed config) always
  print. Closes #268.

## [0.6.29] - 2026-05-30

### Changed

- `wait_for_sync` now polls with adaptive backoff instead of a fixed
  `check_interval` after every missed convergence check. The inter-attempt
  sleep starts at `initial_check_interval` (default `0.05s`) and grows
  geometrically by `backoff_factor` (default `2.0`), capped at
  `check_interval`. Fast syncs that miss the first check are caught in
  ~50–150ms instead of rounding up to a full `check_interval`; slow or
  never-converging syncs still poll at no more than `check_interval` in
  steady state, so RPC load on the slow path is unchanged. The existing
  per-attempt jitter for de-syncing parallel node pollers is preserved.
  New optional step fields `initial_check_interval` and `backoff_factor`
  expose the schedule; existing workflows behave the same or faster
  without changes. Closes #267.

## [0.6.23] - 2026-05-27

### Added

- `cascade` boolean field on the `upgrade_group` workflow step
  (default `false`). When `true`, dispatches the same
  `CascadeTargetApplicationSet` op as `cascade_namespace_application`
  but lets the step accept an optional `migrate_method` for callers
  that need cascade + per-context migration in a single step. Carries
  the same `calimero-client-py >= 0.6.15` pre-flight guard the
  dedicated cascade step uses, gated behind `cascade=true` so existing
  non-cascade callers still work on older client-py installs.
  Closes the gap that left
  `workflows/app-migration/01-namespace-cascade-migration.yml` (in
  `calimero-network/core`) silently dropping `cascade: true` and
  failing on the server-side "no contexts to upgrade" check.

## [0.6.22] - 2026-05-27

### Added

- `workflow-examples/workflow-cascade-namespace-example.yml` — smoke
  test exercising `cascade_namespace_application` end-to-end against a
  real merod container. Sibling of `workflow-group-upgrade-example.yml`,
  uses the same kv_store v1/v2 binaries.
- `cascade_namespace_application` workflow step type. Submits a
  `CascadeTargetApplicationSet` governance op against a namespace,
  fanning the target-application change out to every matching descendant
  subgroup + context in a single sync round (calimero-network/core#2493).
  Wraps the same `upgrade_group` RPC as `UpgradeGroupStep` but with
  `cascade=True`; takes `node`, `namespace_id`, `target_application_id`,
  and optional `migrate_method`. Requires calimero-client-py >= 0.6.15.

### Changed

- Bumped minimum `calimero-client-py` to 0.6.15 (`upgrade_group` now
  accepts the `cascade` kwarg; published in 0.6.15).

## [0.6.17] - 2026-05-23

### Added

- Network fault-injection workflow step family. `pause_container` /
  `unpause_container` simulate laptop sleep / Tauri App Nap;
  `restart_container` (defaults `wait_healthy: true`) simulates a
  boot-node restart; `disconnect_node` / `connect_node` partition + heal
  on the container's Docker network with auto-detection (merobox-cluster
  / calimero_web / bridge) and per-disconnect round-trip state recorded
  via dynamic_values; `inject_network_fault` runs `tc netem` loss/delay
  inside the container. Closes 5/7 primitives from
  calimero-network/merobox#246; the remaining two are tracked in
  calimero-network/merobox#247 (move_to_network multi-network schema)
  and calimero-network/merobox#248 (NAT topology).
- Two node-level config flags on the `nodes:` block: `mdns: false` forces
  `discovery.mdns` off so the rendezvous/relay code path is actually
  exercised; `network_admin: true` (default) adds the NET_ADMIN
  capability to node containers so `inject_network_fault` works out of
  the box. NET_ADMIN is namespaced to the container's netns and cannot
  reach the host network stack. Set `network_admin: false` to opt out.
- Two workflow examples wired into CI: the lightweight
  `workflow-fault-injection-example.yml` demos the primitives with paired
  docker-inspect assertion scripts (`assert-container-state.sh`,
  `assert-container-network.sh`), and
  `workflow-fault-injection-convergence-example.yml` installs kv_store
  and walks a 3-node mesh through partition / pause / restart with
  app-level convergence assertions that catch silent-no-op regressions.
  A third workflow (`workflow-fault-injection-tc-example.yml`) ships as
  copy-pasteable starter for users who run a custom merod image with
  iproute2 installed; excluded from CI since the stock image lacks tc.

## [0.6.16] - 2026-05-21

### Added

- Two new workflow step types — `assert_log_absent` and `assert_log_present`
  — that assert on the contents of a node's docker / binary logs, so
  regression workflows can express log-grep gates inline instead of relying
  on out-of-band CI shell steps to download log artefacts and post-grep
  them. `assert_log_absent` fails if any pattern matches in any of the
  named nodes' logs; `assert_log_present` fails unless every pattern has
  at least `min_matches` hits aggregated across the union of named nodes.
  Shared schema knobs: `regex` (default false, literal substring), `tail_lines`
  (default unbounded), `case_sensitive` (default true), `min_matches`
  (default 1, present-only). Empty `nodes:` list resolves to all running
  nodes at execute time. Docker mode uses `container.logs(tail=...,
  timestamps=False)`; binary mode uses `BinaryManager.get_node_logs`.
  Closes calimero-network/merobox#243; unblocks restoring the regression
  gates lost when calimero-network/core#2431 folded the auto-follow
  workflows into the e2e matrix.

## [0.6.14] - 2026-05-14

### Changed

- Re-release wave alongside #239's `join_subgroup_inheritance` step
  (already covered in the 0.6.13 entry). Applies repository-wide
  `black` formatting and bumps the version stamp; no functional
  changes beyond what 0.6.13 already shipped.

## [0.6.15] - 2026-05-20

### Added

- New `set_member_auto_follow` workflow step wrapping core's
  `PUT /admin-api/groups/:group_id/members/:identity/auto-follow` endpoint
  (calimero-network/core#2427 + #2430). Lets workflows toggle a member's
  per-group `auto_follow.contexts` / `auto_follow.subgroups` flags
  declaratively — previously these flags were only ever set inside core's
  TEE fleet-join path with both hardcoded to `true`, leaving the bucket
  uncovered by every existing e2e workflow (the gap that let
  calimero-network/core#2422's auto-follow regression slip past). Authorized
  by group admin (any `member_id`) or by the target itself (self-setting);
  apply path enforces admin-or-self. Bumps the `calimero-client-py` floor
  from `>=0.6.11` to `>=0.6.13` so installs auto-pull the new Python
  binding the step depends on. The step graceful-degrades on older client
  releases by surfacing an actionable `(requires >= 0.6.13)` message
  outside the API-call try/except.

## [0.6.13] - 2026-05-14

### Fixed

- Worker containers in multi-node clusters now get the same graceful-shutdown
  window as the seed, configurable up to whatever the workload needs. The
  previous fixed 5s drain + 10s `container.stop` (15s total) was enough for
  the seed (node-1) but too short for workers running heavier profiling traps:
  their `perf record` mmap rings never finished flushing before SIGKILL, so
  worker-side `perf-*.data` and flamegraph artifacts never reached the bind
  mount. Adds `MEROBOX_STOP_TIMEOUT` / `MEROBOX_DRAIN_TIMEOUT` env vars and
  matching `--timeout` / `--drain-timeout` CLI flags on `merobox stop`,
  plumbed through `DockerManager` and `BinaryManager`. Explicit caller-set
  values still win over env, and non-numeric env values fall back to the
  default rather than aborting cleanup. Resolves
  [#237](https://github.com/calimero-network/merobox/issues/237).

## [0.6.12] - 2026-05-12

### Fixed

- `expected_failure: true` is now honored by the `group_management.py` step
  classes — it was previously a silent no-op on most of them. Wired into
  `RemoveGroupMembersStep`, `ListGroupMembersStep`, `UpdateMemberRoleStep`,
  `SetMemberCapabilitiesStep`, `GetMemberCapabilitiesStep`,
  `SetDefaultCapabilitiesStep`, `SetSubgroupVisibilityStep`, `GetGroupInfoStep`,
  `ListGroupContextsStep`, `DeleteGroupStep`, `DeleteNamespaceStep`,
  `DeleteContextStep`, and `UninstallApplicationStep`, matching the pattern
  already used by the `Leave*` steps (on a failure path → `_report_expected_failure`
  and return success; on the success path → `_report_unexpected_success` warn).
  Fixes [#214](https://github.com/calimero-network/merobox/issues/214).

## [0.6.11] - 2026-05-12

### Changed

- Replaced the group-scoped *alias* workflow steps (`set_group_alias`,
  `set_member_alias`) with generic *metadata-record* steps, mirroring
  calimero-network/core#2338 (which removed the group alias feature and added a
  `MetadataRecord` — `{ name, data, updatedAt, updatedBy }` — on groups, group
  members, and group-registered contexts). New step types:
  `set_group_metadata` / `get_group_metadata`, `set_member_metadata` /
  `get_member_metadata`, `set_context_metadata` / `get_context_metadata`. The
  `set_*` steps take an optional `record_name` (string — a dedicated key,
  distinct from the step's `name` label), optional `data` (string→string map),
  and optional `requester` (admin public key). The `get_*` steps store the
  `{ "data": <MetadataRecord|null> }`
  response and expose it for `outputs:` / `json_assert` the same way
  `get_group_info` does. Bumps the `calimero-client-py` pin to `>=0.6.10`
  (which adds the matching client methods).

## [0.6.10] - 2026-05-12

### Added

- `--merod-args` flag for `merobox bootstrap run` (binary / `--no-docker` mode
  only): forwards arbitrary arguments to each `merod run` invocation, e.g.
  `--merod-args="--sync-strategy delta --state-sync-strategy hash"`. Parsed with
  `shlex.split()`, ignored (with a warning) outside `--no-docker` mode, and
  threaded through both individual and count-based / `restart: true` node
  starts.
- `stop_node` and `start_node` workflow steps for mid-workflow node lifecycle
  control (benchmarking, failure/recovery testing, freeing resources). Each
  accepts a single node name or a list. `stop_node` is idempotent for
  already-stopped nodes; `start_node` reuses the workflow's `nodes:` config, is
  idempotent for already-running nodes, and supports an optional readiness wait
  (`wait_for_ready`, default `true` — a timeout fails the step — and
  `wait_timeout`, default 30s) that connects to the RPC port and probes the
  admin health endpoint. Resolves
  [#143](https://github.com/calimero-network/merobox/pull/143).

### Fixed

- `stop_all_nodes: false` (also the default) now actually leaves nodes running
  after `merobox bootstrap run` exits. Previously the manager's `atexit` handler
  unconditionally tore down every container it had started, so the "Step 5:
  Leaving nodes running" path was immediately undone on process exit — the only
  workaround was to background the run and `kill -9` it before `atexit` could
  fire. The bootstrap executor now calls `manager.keep_resources_on_exit()` when
  `stop_all_nodes` is falsy, suppressing the `atexit` teardown (SIGINT/SIGTERM
  cleanup is unchanged — interrupting a run still stops the nodes). Resolves
  [#227](https://github.com/calimero-network/merobox/issues/227).
- Multi-node clusters now wire up static bootstrap peers instead of depending on
  mDNS-only discovery over Docker's default bridge. When 2+ nodes are started
  (and `MEROBOX_LEGACY_CLUSTER_NETWORKING` is not set), merobox now: (1) attaches
  the nodes to a dedicated user-defined bridge network (`merobox-cluster`) for
  isolation — except when the auth/Traefik stack is enabled, in which case the
  existing `calimero_web` network is used; (2) reads each node's libp2p peer ID
  from its `config.toml` (written by `merod init`) and its container IP on the
  cluster network, and wires every node's `bootstrap.nodes` to its siblings as
  `/ip4/<container-ip>/tcp/2428/p2p/<peer_id>` (+ `quic-v1`) — IPs, not
  `/dns4/<container>`, because merod's libp2p swarm is built without a DNS
  transport — appended after any explicit `bootstrap_nodes:` from the workflow,
  then restarts the containers so the new config takes effect (mDNS stays enabled
  as a fallback; the rendezvous config is untouched); and (3) blocks until every
  node reports `count-1` connected peers via `GET /admin-api/peers`, failing the
  run if the cluster never connects (timeout overridable via
  `MEROBOX_CLUSTER_PEER_TIMEOUT`, default 60s). Previously, `--e2e-mode` clusters
  (used by the `core` `fuzzy-load-test` CI) emptied `bootstrap.nodes` and pointed
  discovery at a rendezvous namespace with no rendezvous server, leaving mDNS as
  the only live peer-discovery path, so the broadcast fast-path stayed cold and
  every cross-node update leaned on periodic sync (slower, and a narrower code
  path). Resolves [#231](https://github.com/calimero-network/merobox/issues/231).
- `merobox health` now reports the connected-peer count correctly. The
  `GET /admin-api/peers` endpoint returns `{"count": N}`, but `extract_peers_count`
  only understood a `peers` list, so it always showed `0` peers.

### CI

- The Docker workflow jobs and the integration-test job now stream each node
  container's logs to disk (`docker logs -f`, surviving `merobox stop`/`nuke`)
  and upload them as artifacts (`node-logs-*`), so failures — and questions like
  "is the gossipsub mesh forming?" — can be diagnosed from CI without a re-run.
  Mirrors core's `fuzzy-load-test` log capture. New helper:
  `.github/scripts/capture-node-logs.sh`.

## [0.6.9] - 2026-05-09

### Fixed

- `auth_service: true` no longer breaks CORS preflight on `/admin-api`,
  `/jsonrpc`, `/ws`, and `/sse`. The Traefik forward-auth middleware was
  rejecting `OPTIONS` preflights (browsers don't send `Authorization`
  on preflights) and the resulting auth-service response lacked
  `Access-Control-Allow-*` headers, so cross-origin browser clients
  could not reach a node fronted by the auth proxy. Per-node
  `*-preflight` routers now match `Method(`OPTIONS`)` at priority 300
  and apply only the CORS headers middleware, skipping forward-auth.
  Resolves [#228](https://github.com/calimero-network/merobox/issues/228).

## [0.6.8] - 2026-05-07

### Changed

- Require `calimero-client-py>=0.6.8`. The 0.6.8 client-py release is
  pegged to [calimero/core#2292](https://github.com/calimero-network/core/pull/2292)'s
  branch so it knows about the new `application_id` field on
  `SignedGroupOpenInvitation`. Earlier client-py versions silently
  drop the field during typed deserialize, which propagates the
  pre-existing subgroup state-hash divergence the PR is fixing.

## [0.6.7] - 2026-05-07

### Changed

- Require `calimero-client-py>=0.6.7`. The 0.6.7 client-py release
  flips its core git deps back to `master` (after
  [calimero/core#2289](https://github.com/calimero-network/core/pull/2289)
  landed), so it's the canonical post-rename release for ongoing use.
  No functional difference from 0.6.6 (which was pegged to the same
  pre-merge code via the PR branch); 0.6.7 is just durable.

## [0.6.6] - 2026-05-07

### Changed

- Require `calimero-client-py>=0.6.6`. The 0.6.6 client-py release ships
  the `contextStateHash` rename + new `groupStateHash` field. Earlier
  client-py versions can no longer decode group/context info responses
  from a calimero node built from
  [calimero/core#2289](https://github.com/calimero-network/core/pull/2289).
- Removed the transitional `rootHash` fallback in `wait_for_sync`. With
  client-py 0.6.6 pinned, the response struct is strictly typed against
  `contextStateHash`, so the fallback path is dead code.

## [0.6.5] - 2026-05-07

### Changed

- `wait_for_sync` step extended to accept optional `context_id` and/or
  `group_id`. Specify `context_id` to wait for `contextStateHash`
  convergence (storage state), `group_id` to wait for `groupStateHash`
  convergence (governance state), or both to wait for both. At least
  one is required. Implements A2 of the cross-DAG authorization
  roadmap in [calimero/core](https://github.com/calimero-network/core).
- `wait_for_sync` reads `contextStateHash` (renamed from `rootHash` in
  [calimero/core#2289](https://github.com/calimero-network/core/pull/2289)),
  with a transitional fallback to the legacy `rootHash` so this
  release works against released calimero binaries that pre-date the
  rename. The fallback can be removed in a follow-up once the rename
  has shipped in a calimero release.
- `outputs` references to `root_hash` continue to work via a
  backwards-compatible top-level alias; the new `context_state_hash`
  and `group_state_hash` keys are the canonical names going forward.

## [0.6.0] - 2026-04-23

### Added

- 9 new workflow step types wrapping admin-API methods that
  `calimero-client-py` exposes but merobox didn't previously surface:
  `set_group_alias`, `set_member_alias` (new file `steps/group_alias.py`);
  `update_group_settings`, `detach_context_from_group`, `sync_group`
  (new file `steps/group_governance.py`); `register_group_signing_key`,
  `upgrade_group`, `get_group_upgrade_status`, `retry_group_upgrade`
  (new file `steps/group_upgrade.py`). Closes
  [#205](https://github.com/calimero-network/merobox/issues/205).
- Optional `requester` field on `delete_context`, `delete_group`,
  `delete_namespace` steps. The server requires an admin requester to
  delete group-registered contexts and admin-guarded groups; the step
  accepts a public-key string and forwards it to the client.
- Two runnable example workflows exercising the full surface:
  `workflow-examples/workflow-group-admin-example.yml` (aliasing +
  policy + member roles + detach + remove + sync + teardown) and
  `workflow-examples/workflow-group-upgrade-example.yml` (register
  signing key + upgrade + status + retry + teardown, using the new
  `workflow-examples/res/kv_store_v2.wasm` bundle).

### Fixed

- 13 existing step types (`remove_group_members`, `list_group_members`,
  `list_group_contexts`, `update_member_role`, `set_member_capabilities`,
  `get_member_capabilities`, `set_default_capabilities`,
  `set_default_visibility`, `get_group_info`, `delete_group`,
  `delete_namespace`, `delete_context`, `uninstall_application`) had
  working `Step` classes and dispatcher entries but missing entries in
  `STEP_TYPE_MODELS`, so `validate_workflow_step` silently skipped
  Pydantic validation for them. Bad YAML for these step types now fails
  at YAML-load with a clear error instead of reaching runtime.
- CLI `bootstrap validate` command (`validate/validator.py`) was missing
  all 13 Bucket-B step types too — it had its own step-class dispatch
  chain that stopped at `add_group_members`. Now covers all 22.
- `GetNamespaceIdentityStep` and `GetGroupUpgradeStatusStep` no longer
  emit "No outputs configured" warnings for callers that don't use the
  `outputs:` mapping — the `_export_variables` call is now gated on
  `"outputs" in self.config`.

### Dependencies

- Bumps `calimero-client-py` lower bound from `>=0.6.0` to `>=0.6.3`
  in `pyproject.toml`. 0.6.3 introduces the `requester` parameter on
  `delete_context` / `delete_group` / `delete_namespace` PyClient
  methods (upstream PR
  [calimero-client-py#36](https://github.com/calimero-network/calimero-client-py/pull/36))
  and routes `delete_namespace` through the correct
  `/admin-api/namespaces/:id` HTTP endpoint. Requires core
  `0.10.1-rc.31+` to expose the dedicated `delete_namespace` actor
  handler (upstream PRs
  [core#2227](https://github.com/calimero-network/core/pull/2227)
  and [core#2232](https://github.com/calimero-network/core/pull/2232)).

## [0.5.2] - 2026-04-23

### Fixed

- Add `CAP_PERFMON` to the container capability list so `perf record`
  works inside Docker containers running the profiling image. Without
  it, `sys_perf_event_open()` fails with `EPERM` even when `linux-tools`
  is correctly installed, which blocks CPU-profile collection in
  calimero-network/core's fuzzy-load-test. `CAP_PERFMON` is the narrow
  Linux 5.8+ capability specifically for `perf_events` — preferred over
  `CAP_SYS_ADMIN`. File-permission caps are unchanged.

## [0.5.0] - 2026-04-22

### Breaking changes

- Removed `nest_group` and `unnest_group` workflow step types and the
  matching `merobox group nest` / `merobox group unnest` CLI commands.
  These primitives produced orphan group state, which is no longer
  expressible in the upstream calimero-network/core API
  ([core PR #2200](https://github.com/calimero-network/core/pull/2200)).
- Removed `NestGroupStepConfig` and `UnnestGroupStepConfig` pydantic
  schemas and their entries in `SUPPORTED_STEP_TYPES`.

### Added

- `reparent_group` workflow step type — atomically moves
  `child_group_id` to `new_parent_id` within the same namespace.
  Replaces the old nest+unnest two-step pattern. Required fields:
  `node`, `child_group_id`, `new_parent_id`.
- `merobox group reparent <group_id> <new_parent_id>` CLI command.
- 12 new unit tests in `test_group_steps.py` covering validation,
  pydantic schema, and absence assertions for the removed step types.

### Migration

Replace this old YAML pattern:

```yaml
- type: unnest_group
  parent_group_id: '{{old_parent}}'
  child_group_id: '{{child}}'
- type: nest_group
  parent_group_id: '{{new_parent}}'
  child_group_id: '{{child}}'
```

With:

```yaml
- type: reparent_group
  child_group_id: '{{child}}'
  new_parent_id: '{{new_parent}}'
```

Note that `delete_group` now cascades upstream — it will delete the
target group, all descendants, and every context registered in the
subtree. To preserve a context before deletion, use
`detach_context_from_group` first.

## [0.4.6] - 2026-04-21

### Added
- `expected_failure: true` is now honored on non-`call` step types. Previously
  only the `call` step consulted the flag; every other step type silently
  treated any API error as a hard workflow failure, making negative-path
  assertions impossible for things like joining the wrong context, creating
  against a nonexistent namespace, or installing a bad payload. Updated step
  types: `join_context`, `join_namespace`, `create_context`, `create_namespace`,
  `create_namespace_invitation`, `install_application`, `create_group_in_namespace`,
  `add_group_members`. Matches the existing `call` semantic: a real failure is
  treated as success; an unexpected success warns but does not fail the step.
- New BaseStep helpers `_is_expected_failure()`, `_report_expected_failure()`,
  and `_report_unexpected_success()` so future step types can opt into the
  same behaviour in two lines.
- New `workflow-expected-failure-steps-example.yml` under `workflow-examples/`,
  auto-picked up by the CI matrix so the behaviour stays verified on every PR.

## [0.4.5] - 2026-04-20

### Fixed
- Graceful shutdown no longer crashes with `RuntimeError: cannot schedule new
  futures after interpreter shutdown` when cleanup runs from the `atexit`
  path. `_graceful_stop_containers_batch` now catches the error raised by
  `ThreadPoolExecutor.submit()` during interpreter finalization and falls
  back to a sequential stop, so containers are properly torn down instead of
  being left running between workflow runs (regression from #198).

## [0.4.4] - 2026-04-13

### Added
- New `add_group_members` workflow step for adding members to a subgroup.
- `create_group_in_namespace` step now exports its `group_id` for downstream steps.
- `workflow-subgroups-example.yml` demonstrating the full subgroup flow
  (create namespace → create subgroup → add members → create context in subgroup).

### Changed
- Docker CI now runs the groups and subgroups example workflows
  (previously skipped because the edge image lagged behind core master;
  fixes from core PR #2127 are now in edge).

### Fixed
- `add_group_members` step is now wired into the main executor dispatch.
- Pin `rich<14.3.4` to avoid lazy-import breakage on Linux.
- Install `merobox` in dev mode for the `test-unit` CI job.

### Breaking
- **NEAR blockchain removed**: All NEAR Sandbox, relayer, and blockchain functionality has been removed. Context management is now fully local (P2P gossip).
- **`--enable-relayer` / `--no-enable-relayer` CLI flags**: Removed.
- **`--chain-id` CLI flag**: Removed.
- **`chain_id` in workflow YAML**: No longer required or used in node config.
- **`near_devnet` in workflow YAML**: No longer used.
- **`protocol` parameter**: Removed from `context create` command.
- **`merobox/commands/near/`**: Entire module deleted (sandbox, client, contracts, utils).
- **`workflow-proposals-example.yml`**: Deleted (proposals were blockchain-only).
- **`testing.cluster()` / `testing.workflow()`**: `near_devnet` and `chain_id` parameters removed.

### Changed
- **Invitations**: `invite_identity` step type renamed to `invite_open` in workflows. All invitations now use signed open invitations via `calimero-client-py`.
- **`valid_for_blocks`**: Renamed to `valid_for_seconds` throughout (old name accepted as fallback in workflow configs).
- **`create_open_invitation_via_admin_api`**: Now delegates to `invite_identity_via_admin_api` (deduplicated).

### Removed
- `merobox/commands/near/` (sandbox.py, client.py, contracts.py, utils.py)
- `merobox/tests/unit/test_near_devnet.py`
- `merobox/tests/unit/test_near_sandbox.py`
- `workflow-examples/workflow-proposals-example.yml`
- NEAR-specific constants (`NEAR_SANDBOX_RPC_PORT`, `DEFAULT_PROTOCOL`, `PROTOCOL_NEAR`, etc.)

## [0.3.7] - 2026-02-12

### Breaking
- **testing**: `merobox.testing.cluster()` and `merobox.testing.workflow()` now default `near_devnet=True` (local sandbox). Code that relied on the previous default (relayer/testnet) must pass `near_devnet=False` explicitly.

### Added
- **Auto-download NEAR contracts**: `ensure_calimero_near_contracts()`; default contracts version 0.6.0; optional `contracts_dir` with env override `CALIMERO_CONTRACTS_VERSION`.
- **Open invitation workflow**: `workflow-open-invitation-log-analysis.md`; join_open step surfaces underlying API/exception error and traceback.

### Changed
- **Default local NEAR sandbox**: Workflow runs use a local NEAR sandbox by default (no flag). Use `--enable-relayer` to use the relayer/testnet.
- **CLI**: Expose only node-management commands (bootstrap, health, logs, nuke, remote, run, stop); application, blob, call, context, identity, install, join, list, proposals no longer in main CLI.
- Consolidated protocol behavior to NEAR-only across CLI and workflow guidance.
- `context create` usage documents NEAR as default; workflow runs use local sandbox by default or `--enable-relayer` for testnet.

### Removed
- `--near-devnet` flag (replaced by default sandbox + `--enable-relayer`).
- `workflow-examples/scripts/download_contracts.sh` (contracts auto-downloaded).
- Non-NEAR protocol examples from user-facing docs; non-NEAR E2E/default guidance references.

## [0.2.10] - 2024-11-27

### Added
- **Dynamic Port Allocation for Binary Mode**: Added dynamic port allocation in e2e mode to prevent port conflicts when multiple test workflows run concurrently.
  - When `--e2e-mode` is enabled, binary mode now finds available ports dynamically instead of using fixed port ranges.
  - Starts port search from 3000 to avoid common system port conflicts.
  - Prevents "Address already in use" errors in CI environments where multiple workflows run simultaneously.

### Fixed
- **Port Conflicts in CI**: Resolved persistent "Address already in use (os error 98)" errors that were causing test failures in GitHub Actions.
- **Concurrent Test Execution**: Tests can now run concurrently without port conflicts, improving CI reliability and speed.

## [0.2.9] - 2024-11-27

### Added
- **Ethereum Local Devnet Support**: Added local Anvil devnet configuration to e2e defaults
  - `context.config.ethereum.contract_id = "0x5FbDB2315678afecb367f032d93F642f64180aa3"` (local contract)
  - `context.config.signer.self.ethereum.sepolia.rpc_url = "http://127.0.0.1:8545"` (local Anvil)
  - `context.config.signer.self.ethereum.sepolia.account_id = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"` (test account)
  - Uses same configuration as e2e tests for consistency and reliability

### Fixed
- **Ethereum Relayer Timeouts**: Resolved by using local Anvil devnet instead of public Sepolia testnet
- **Test Reliability**: Ethereum tests now use fast, reliable local blockchain instead of congested public network
- **Configuration Consistency**: Merobox now matches e2e test Ethereum setup exactly

### Changed
- **E2E Mode**: When `--e2e-mode` is enabled, Ethereum tests automatically use local devnet configuration
- **CI Integration**: Works with existing GitHub workflow that already deploys Anvil devnet

## [0.2.8] - 2024-11-27

### Added
- **`--e2e-mode` Flag**: Added optional flag to enable e2e-style test defaults
  - Only applies aggressive sync settings and test isolation when explicitly requested
  - Prevents e2e defaults from being applied by default to all workflows
  - Maintains backward compatibility for existing workflows

### Changed
- **E2E Defaults**: Made e2e-style configuration optional instead of always applied
  - `bootstrap.nodes = []` (disable bootstrap nodes)
  - `discovery.rendezvous.namespace = calimero/merobox-tests/{workflow_id}` (unique namespace)
  - `sync.timeout_ms = 30000`, `sync.interval_ms = 500`, `sync.frequency_ms = 1000` (aggressive sync)
  - Only applied when `--e2e-mode` flag is used

### Fixed
- **Default Behavior**: Restored normal merobox behavior for regular workflows (no forced e2e defaults)
- **GitHub Workflow**: Updated CI to use `--e2e-mode` flag for proper test isolation

## [0.2.7] - 2024-11-27

### Added
- **Aggressive Sync Settings**: Added e2e-style sync configuration for improved test reliability
  - `sync.timeout_ms=30000` (30s timeout, matches production)
  - `sync.interval_ms=500` (500ms between syncs, very aggressive for tests)
  - `sync.frequency_ms=1000` (1s periodic checks, ensures rapid sync in tests)

### Fixed
- **Connection Stability**: Improved node synchronization and connection stability by matching e2e test sync settings
- **TLS Connection Issues**: Resolved TLS close_notify errors by improving sync timing and reliability

## [0.2.6] - 2024-11-26

### Added
- **E2E-Style Test Isolation**: Automatic application of e2e-style network configuration for reliable CI testing
- **Unique Rendezvous Namespaces**: Each workflow gets a unique namespace (`calimero/merobox-tests/{workflow_id}`) for test isolation
- **Bootstrap Node Isolation**: Automatic disabling of production bootstrap nodes for test isolation

### Changed
- **Default Network Configuration**: All workflows now use e2e-style network isolation by default
- **Test Reliability**: Significantly improved 3-node test reliability in CI environments
- **Peer Discovery**: Sets a unique rendezvous namespace per workflow (note: no
  rendezvous server is started by merobox, so in practice CI clusters relied on
  mDNS until the dedicated cluster networking added under #231)
- **Sync Settings**: Uses regular production sync defaults (no aggressive overrides needed)

### Technical Details
- **Network Isolation Only**: Focus on network-level isolation without sync timing changes
- **Automatic Config Override**: Node configurations are automatically modified after initialization
- **Workflow ID Generation**: Each workflow gets a unique 8-character ID for namespace isolation
- **TOML Configuration**: Added toml dependency for config file manipulation
- **Backward Compatibility**: Existing workflows work unchanged with improved reliability

### Dependencies
- **Added**: `toml>=0.10.2` for configuration file management

## [0.1.21] - 2024-12-19

### Changed
- **Version Bump**: Updated to version 0.1.21 for release

## [0.1.11] - 2024-12-19

### Added
- **Docker Image Force Pull**: New `force_pull_image` workflow configuration option
- **CLI Force Pull Flag**: `--force-pull` option for the `run` command
- **Automatic Image Management**: Smart Docker image pulling with remote detection
- **Image Pull Progress**: Real-time feedback during Docker image operations

### Changed
- **Image Handling**: Enhanced Docker image management with automatic remote detection
- **Workflow Configuration**: Added `force_pull_image` flag to workflow YAML files
- **Documentation**: Comprehensive documentation for Docker image management features

### Technical Details
- **Remote Detection**: Automatically identifies remote images (containing `/` and `:`)
- **Smart Pulling**: Only pulls images when necessary, with force pull override options
- **Error Handling**: Graceful fallback when image operations fail
- **Integration**: Seamlessly integrated into both CLI commands and workflow execution

## [0.1.10] - 2024-12-19

### Fixed
- **Documentation Distribution**: Fixed PyPI package to include docs/ folder with all documentation files
- **Package Structure**: Moved docs/ folder into merobox package for proper distribution
- **Documentation Links**: Updated README.md to reflect new documentation structure

## [0.1.9] - 2024-12-19

### Added
- **Bootstrap Command Refactoring**: Split bootstrap command into logical subcommands (run, validate, create-sample)
- **Modular Architecture**: Reorganized bootstrap steps into separate modules for better maintainability
- **Input Validation Framework**: Comprehensive validation for all step types with required field checking
- **Explicit Export Enforcement**: Variables are now only exported when explicitly configured in outputs
- **Code Formatting**: Added Black formatter with GitHub Actions CI integration
- **Documentation Reorganization**: Created docs/ folder with topic-specific documentation files

### Changed
- **Command Structure**: `merobox bootstrap` now requires subcommand (run, validate, create-sample)
- **Import Strategy**: Converted all imports to absolute imports for better package compatibility
- **Validation Logic**: Moved validation functions to dedicated validator module
- **Step Execution**: Separated workflow execution logic into dedicated run module
- **CLI Organization**: Better separation of concerns between command definition and execution logic

### Fixed
- **Import Paths**: Resolved dynamic import issues in bootstrap executor
- **Validation Errors**: Fixed missing field validation for all step types
- **Documentation Links**: Ensured PyPI compatibility for documentation structure

## [0.1.8] - 2024-12-19

### Added
- **PyPI Release**: Package now available on PyPI for easy installation
- **Makefile Automation**: Complete build and release automation using Makefile
- **Release Documentation**: Comprehensive release process documentation in README
- **Development Workflow**: Streamlined development and release process

### Changed
- **Package Structure**: Moved commands/ into merobox/commands/ for proper package layout
- **Build System**: Switched to setup.py for better metadata version control
- **CLI Entry Point**: Removed duplicate merobox_cli.py, using merobox/cli.py as canonical entry point
- **Documentation**: Consolidated all documentation into comprehensive README.md

### Fixed
- **Embedded Placeholders**: Fixed dynamic variable resolution for placeholders within strings (e.g., `complex_key_{{current_iteration}}_b`)
- **Variable Resolution**: Added recursive args processing for dynamic variables in ExecuteStep
- **Repeat Step Outputs**: Implemented custom outputs for repeat steps with proper iteration variable mapping
- **Metadata Compatibility**: Resolved PyPI upload issues by using compatible metadata version 2.1
- **Import Strategy**: CLI now supports both package and direct script execution

### Removed
- **Duplicate CLI**: Removed redundant merobox_cli.py entry point
- **Redundant Scripts**: Removed scripts/publish.py in favor of Makefile automation
- **pyproject.toml**: Simplified to use only setup.py for better compatibility

### Technical Details
- **Metadata Version**: Fixed from 2.4 to 2.1 for PyPI compatibility
- **Package Layout**: Standard Python package structure with merobox/commands/ subpackage
- **Build Commands**: `make build`, `make check`, `make publish` for streamlined workflow
- **Development Mode**: `make install-dev` for local development installation

## [0.1.7] - 2024-12-19

### Added
- **Dynamic Variable Resolution**: Support for placeholders like `{{variable_name}}` in workflow configurations
- **Workflow Orchestration**: Multi-step workflow execution with YAML configuration
- **Bootstrap System**: Automated workflow execution engine
- **Step Types**: Install, context, identity, invite, join, call, wait, and repeat steps
- **Embedded Placeholder Support**: Variables can be embedded within strings (e.g., `key_{{iteration}}_suffix`)

### Changed
- **Package Structure**: Reorganized commands into logical modules
- **CLI Framework**: Enhanced Click-based command-line interface
- **Documentation**: Added comprehensive workflow examples and usage guides

### Fixed
- **Variable Replacement**: Corrected logic for resolving dynamic values in workflow steps
- **Iteration Handling**: Fixed repeat step variable mapping and output processing
- **Args Processing**: Added recursive processing for dynamic values in function call arguments

## [0.1.6] - 2024-12-18

### Added
- **Workflow Support**: Basic workflow execution capabilities
- **Dynamic Variables**: Initial placeholder replacement system
- **Step Framework**: Extensible step execution architecture

### Changed
- **CLI Structure**: Reorganized command structure for better maintainability
- **Error Handling**: Improved error reporting and user feedback

## [0.1.5] - 2024-12-17

### Added
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Execution**: Execute smart contract functions via JSON-RPC

### Changed
- **Node Communication**: Enhanced JSON-RPC client for better node interaction
- **Command Structure**: Reorganized CLI commands for logical grouping

## [0.1.4] - 2024-12-16

### Added
- **Multi-Node Support**: Start and manage multiple Calimero nodes
- **Port Management**: Automatic port detection and assignment
- **Health Monitoring**: Node health status checking

### Changed
- **Docker Integration**: Improved container management and monitoring
- **Error Handling**: Better error reporting and recovery

## [0.1.3] - 2024-12-15

### Added
- **Application Installation**: Install WASM applications on nodes
- **Log Management**: View and follow node logs
- **Data Cleanup**: Complete node data removal with nuke command

### Changed
- **CLI Interface**: Enhanced command-line interface with better help and options
- **Docker Management**: Improved container lifecycle management

## [0.1.2] - 2024-12-14

### Added
- **Basic Node Management**: Start, stop, and list Calimero nodes
- **Docker Integration**: Container-based node deployment
- **Configuration Management**: Customizable node settings

### Changed
- **Project Structure**: Initial package organization
- **Dependencies**: Added core dependencies for Docker and CLI operations

## [0.1.1] - 2024-12-13

### Added
- **Project Foundation**: Initial project setup and structure
- **Basic CLI Framework**: Click-based command-line interface foundation
- **Documentation**: Basic README and project documentation

## [0.1.0] - 2024-12-12

### Added
- **Initial Release**: Project creation and basic structure
- **License**: MIT License for open source development
- **Project Configuration**: Basic setup.py and project metadata
