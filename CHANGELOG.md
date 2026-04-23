# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Peer Discovery**: Uses rendezvous-based discovery instead of unreliable mDNS in CI
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
