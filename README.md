# Merobox CLI

A comprehensive Python CLI tool for managing Calimero nodes in Docker containers and executing complex blockchain workflows.

## 📚 Table of Contents

- [🚀 Quick Start](#-quick-start)
- [✨ Features](#-features)
- [🔐 Auth Service Integration](#-auth-service-integration)
- [📖 Workflow Guide](#-workflow-guide)
- [🎯 Local Blockchain Environments](#-local-blockchain-environments)
- [🔧 API Reference](#-api-reference)
- [🛠️ Development Guide](#️-development-guide)
- [❓ Troubleshooting](#-troubleshooting)
- [🏗️ Project Structure](#️-project-structure)
- [📋 Requirements](#-requirements)
- [🚀 Releases & Publishing](#-releases--publishing)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🆘 Support](#-support)

## 🚀 Quick Start

### Installation

```bash
# From PyPI
pipx install merobox

# From source
git clone https://github.com/calimero-network/merobox.git
cd merobox
pipx install -e .

# From Homebrew
brew install merobox
```

### Basic Usage

```bash
# Start Calimero nodes
merobox run --count 2

# Start nodes with authentication service
merobox run --auth-service

# Check node status
merobox list
merobox health

# Execute a workflow
merobox bootstrap run workflow.yml

# Run workflow against local NEAR Devnet (no testnet URL and no testnet tokens required)
merobox bootstrap run workflow.yml \
  --near-devnet \
  --contracts-dir ./contracts/res

# Run everything against the local mock relayer
merobox bootstrap run workflow.yml --mock-relayer

# Stop all nodes and auth services
merobox stop --all
```

## ✨ Features

- **Node Management**: Start, stop, and monitor Calimero nodes in Docker
- **Auth Service Integration**: Traefik proxy and authentication service with nip.io DNS
- **Workflow Orchestration**: Execute complex multi-step workflows with YAML
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Calls**: Execute smart contract functions via JSON-RPC
- **Dynamic Variables**: Advanced placeholder resolution with embedded support
- **Local NEAR Devnet**: Use local instance of NEAR blockchain (Sandbox) for zero-cost and quick local testing
- **Mock Relayer Support**: One flag (`--mock-relayer`) spins up ghcr.io/calimero-network/mero-relayer:8ee178e and wires nodes to it

---

## 🔐 Auth Service Integration

Merobox supports integrated authentication services with Traefik proxy and nip.io DNS resolution, enabling secure access to your Calimero nodes through web URLs.

### Quick Start with Auth Service

```bash
# Start a single node with auth service
merobox run --auth-service

# Start multiple nodes with auth service
merobox run --count 2 --auth-service

# Stop everything (nodes + auth services)
merobox stop --all
```

### What Gets Created

When you enable `--auth-service`, merobox automatically creates:

1. **Traefik Proxy** (`proxy` container) - Routes traffic and applies middleware
2. **Auth Service** (`auth` container) - Handles authentication and authorization
3. **Docker Networks**:
   - `calimero_web` - External communication (Traefik ↔ Internet)
   - `calimero_internal` - Secure backend communication (Auth ↔ Nodes)

### URL Access

**With Auth Service:**

- Node URLs: `http://node1.127.0.0.1.nip.io`, `http://node2.127.0.0.1.nip.io`, etc.
- Auth Login: `http://node1.127.0.0.1.nip.io/auth/login`
- Admin Dashboard: `http://node1.127.0.0.1.nip.io/admin-dashboard`

**Without Auth Service:**

- Admin Dashboard: `http://localhost:2528/admin-dashboard`
- Admin API: `http://localhost:2528/admin-api/`

### Workflow Integration

Enable auth service in workflows by adding `auth_service: true`:

```yaml
name: "My Auth Workflow"
description: "Workflow with authentication enabled"

# Enable auth service
auth_service: true

nodes:
  count: 1
  base_port: 2428
  base_rpc_port: 2528
  chain_id: "testnet-1"

steps:
  - name: "Wait for startup"
    type: "wait"
    seconds: 5
```

### Architecture

```
Internet → Traefik (port 80) → Node Containers (calimero_web network)
                              ↓
                           Auth Service (calimero_internal network)
```

- **Public routes**: `/admin-dashboard` (no auth required)
- **Protected routes**: `/admin-api/`, `/jsonrpc`, `/ws` (auth required)
- **Auth routes**: `/auth/login`, `/admin/` (handled by auth service)

---

## 📖 Workflow Guide

### Overview

Merobox workflows are defined in YAML files and executed through the `bootstrap` command. Workflows can include multiple steps like installing applications, creating contexts, managing identities, and executing function calls.

### Workflow Structure

```yaml
name: "Sample Workflow"
nodes:
  - calimero-node-1
  - calimero-node-2

steps:
  - name: "Install Application"
    type: "install"
    node: "calimero-node-1"
    path: "./app.wasm"
    outputs:
      applicationId: "app_id"
```

### Step Types

#### Install Step

Installs WASM applications on Calimero nodes.

```yaml
- name: "Install App"
  type: "install"
  node: "calimero-node-1"
  path: "./application.wasm" # Local path
  # OR
  url: "https://example.com/app.wasm" # Remote URL
  dev: true # Development mode
  outputs:
    applicationId: "app_id"
```

#### Context Step

Creates blockchain contexts for applications.

```yaml
- name: "Create Context"
  type: "context"
  node: "calimero-node-1"
  application_id: "{{app_id}}"
  params:
    param1: "value1"
  outputs:
    contextId: "context_id"
    memberPublicKey: "member_key"
```

#### Identity Step

Generates cryptographic identities.

```yaml
- name: "Create Identity"
  type: "identity"
  node: "calimero-node-2"
  outputs:
    publicKey: "public_key"
```

#### Invite Step

Invites identities to join contexts.

```yaml
- name: "Invite Identity"
  type: "invite"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  grantee_id: "{{public_key}}"
  outputs:
    invitation: "invitation_data"
```

#### Join Step

Joins contexts using invitations.

```yaml
- name: "Join Context"
  type: "join"
  node: "calimero-node-2"
  context_id: "{{context_id}}"
  invitee_id: "{{public_key}}"
  invitation: "{{invitation_data}}"
```

#### Create Mesh Step

Creates a context and connects multiple nodes in a single step. It automatically creates a context on the specified node, generates identities for each target node, sends invitations, and joins all nodes to the context.

```yaml
- name: "Create Mesh"
  type: "create_mesh"
  context_node: "calimero-node-1"
  application_id: "{{app_id}}"
  nodes:
    - calimero-node-2
    - calimero-node-3
  capability: member # Optional, defaults to "member"
  outputs:
    context_id: contextId
    member_public_key: memberPublicKey
```

**What it does:**

- Creates a context on the specified `context_node`
- Creates identities on each node in the `nodes` list
- Invites each node from the context node
- Joins each node to the context

**Parameters:**

- `context_node`: Node where the context will be created
- `application_id`: Application ID to use for context creation
- `nodes`: List of node names to create identities on and join to the context
- `capability`: Optional capability for invitations (defaults to `"member"`).

**Exported variables:**

- `context_id`: The created context ID
- `member_public_key`: The context creator's public key
- `public_key_{node_name}`: Public key for each joined node (e.g., `public_key_calimero-node-2`)
- `public_key`: If only one node is joined, also exported as just `public_key`

#### Execute Step

Executes smart contract functions.

```yaml
- name: "Call Function"
  type: "call"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  method: "set"
  args:
    key: "hello"
    value: "world"
  executor_public_key: "{{member_key}}"
  outputs:
    result: "function_result"
```

**Negative Testing with Expected Failures:**

You can test error scenarios by setting `expected_failure: true`. When enabled, the step will continue even if the call fails, and error details are exported for assertions.

Both simple and complex output syntax are supported for error fields:

```yaml
- name: "Expected Failure - Invalid Method"
  type: "call"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  executor_public_key: "{{member_public_key}}"
  method: "invalid_method_that_does_not_exist"
  args: {}
  expected_failure: true
  outputs:
    # Simple string assignment
    error_code: error_code # JSON-RPC error code
    error_type: error_type # Error type (e.g., "FunctionCallError")
    error_message: error_message # Error message
    error: error # Full error object

    # Complex dict-based assignment (also supported)
    custom_error:
      field: error_type
    custom_error_code:
      field: error_code
      target: error_code_{node_name} # Optional: custom target with node name

- name: "Assert Error Occurred"
  type: assert
  statements:
    - "is_set({{error_type}})"
    - "equal({{error_type}}, FunctionCallError)"
    - "contains({{error_message}}, not found)"
    - "equal({{custom_error}}, FunctionCallError)"
```

**Error Handling:**

- **JSON-RPC Errors**: When a function call succeeds but returns a JSON-RPC error (e.g., invalid method, wrong arguments), error fields are extracted and exported.
- **Network/API Errors**: When the API call itself fails (e.g., invalid context ID, network issues), error information is captured and exported.
- **Unexpected Success**: If `expected_failure: true` but the call succeeds, a warning is shown and error fields are exported as `None`.

See `workflow-examples/workflow-negative-testing-example.yml` for comprehensive examples.

#### Wait Step

Adds delays between steps.

```yaml
- name: "Wait"
  type: "wait"
  seconds: 5
```

#### Wait for Sync Step

Waits for nodes to reach consensus by verifying they have the same root hash. This is more reliable than fixed wait times because it only proceeds once nodes are actually synchronized.

```yaml
- name: "Wait for Nodes to Sync"
  type: "wait_for_sync"
  context_id: "{{context_id}}"
  nodes:
    - calimero-node-1
    - calimero-node-2
  timeout: 30 # Max seconds to wait (default: 30)
  check_interval: 1 # Seconds between checks (default: 1.0)
  trigger_sync: true # Trigger sync before checking (default: true)
  outputs:
    root_hash: "synced_hash"
    elapsed_seconds: "sync_time"
```

**Key Features:**

- Verifies root hash consensus across specified nodes
- Fails workflow if nodes don't sync within timeout
- Provides detailed sync progress logging
- More reliable than fixed wait times
- Captures sync metrics (time, attempts, final hash)

**When to Use:**

- After state-changing operations (set, delete, update)
- After nodes join a context
- Before reading data from different nodes
- In critical workflows where consistency is required

#### Repeat Step

Executes steps multiple times.

```yaml
- name: "Repeat Operations"
  type: "repeat"
  count: 3
  steps:
    - name: "Set Value"
      type: "call"
      node: "calimero-node-1"
      context_id: "{{context_id}}"
      method: "set"
      args:
        key: "iteration_{{current_iteration}}"
        value: "value_{{current_iteration}}"
      executor_public_key: "{{member_key}}"
      outputs:
        result: "iteration_result"
    - name: "Wait"
      type: "wait"
      seconds: 2
  outputs:
    iteration: "current_iteration"
```

#### Fuzzy Test Step

Runs long-duration randomized load tests with weighted operation patterns and assertion-based validation.

```yaml
- name: "Fuzzy Load Test"
  type: "fuzzy_test"
  duration_minutes: 30 # Run for 30 minutes
  context_id: "{{context_id}}"
  success_threshold: 95.0 # Pass if 95%+ assertions succeed

  nodes:
    - name: calimero-node-1
      executor_key: "{{member_public_key}}"
    - name: calimero-node-2
      executor_key: "{{public_key_node2}}"

  operations:
    # Pattern 1: Write and verify (40% of operations)
    - name: "set_and_verify"
      weight: 40
      steps:
        # Resolved args auto-captured as {{fuzzy_key}}, {{fuzzy_value}}
        - type: call
          node: "{{random_node}}" # Random node selection
          method: set
          context_id: "{{context_id}}"
          executor_public_key: "{{random_executor}}"
          args:
            key: "test_{{random_int(1, 1000)}}" # Random generators
            value: "{{uuid}}_{{timestamp}}"

        - type: wait
          seconds: 1

        # Use auto-captured {{fuzzy_key}}
        - type: call
          node: "{{random_node}}"
          method: get
          context_id: "{{context_id}}"
          executor_public_key: "{{random_executor}}"
          args:
            key: "{{fuzzy_key}}"
          outputs:
            retrieved: result

        # Use auto-captured {{fuzzy_value}}
        - type: assert
          non_blocking: true # Failures don't stop the test
          statements:
            - statement: "contains({{retrieved}}, {{fuzzy_value}})"
              message: "Value should match"

    # Pattern 2: Cross-node propagation test (30% of operations)
    - name: "cross_node_sync"
      weight: 30
      steps:
        # Args auto-captured as {{fuzzy_key}}, {{fuzzy_value}}
        - type: call
          node: calimero-node-1
          method: set
          context_id: "{{context_id}}"
          executor_public_key: "{{member_public_key}}"
          args:
            key: "sync_{{random_int(1, 500)}}"
            value: "{{timestamp}}"

        - type: wait
          seconds: 2 # Wait for propagation

        # Use auto-captured {{fuzzy_key}}
        - type: call
          node: calimero-node-2
          method: get
          context_id: "{{context_id}}"
          executor_public_key: "{{public_key_node2}}"
          args:
            key: "{{fuzzy_key}}"
          outputs:
            synced_value: result

        # Use auto-captured {{fuzzy_value}}
        - type: assert
          non_blocking: true
          statements:
            - statement: "contains({{synced_value}}, {{fuzzy_value}})"
              message: "Data should propagate across nodes"

    # Pattern 3: Random reads (30% of operations)
    - name: "random_read"
      weight: 30
      steps:
        - type: call
          node: "{{random_node}}"
          method: get
          context_id: "{{context_id}}"
          executor_public_key: "{{random_executor}}"
          args:
            key: "test_{{random_int(1, 1000)}}"
```

**Random Value Generators:**

- `{{random_int(min, max)}}` - Random integer
- `{{random_string(length)}}` - Random alphanumeric string
- `{{random_float(min, max)}}` - Random float
- `{{random_choice([a, b, c])}}` - Random choice from list
- `{{timestamp}}` - Current Unix timestamp
- `{{uuid}}` - Random UUID
- `{{random_node}}` - Random node from nodes list
- `{{random_executor}}` - Random executor key

**Auto-Captured Arguments:**

When a `call` step executes, its resolved arguments are automatically captured with a `fuzzy_` prefix:

- `args.key` → `{{fuzzy_key}}`
- `args.value` → `{{fuzzy_value}}`
- `args.amount` → `{{fuzzy_amount}}`

This allows subsequent steps to reference the exact values used in previous calls for verification.

**Features:**

- **Application-agnostic**: Works with any smart contract
- **Weighted patterns**: Control operation frequency
- **Non-blocking assertions**: Track failures without stopping
- **Live progress**: Periodic summaries every 60 seconds
- **Detailed reporting**: Final report with pass rates and failure analysis

#### Upload Blob Step

Uploads files to blob storage and captures blob IDs for E2E testing.

```yaml
- name: "Upload File to Blob Storage"
  type: "upload_blob"
  node: "calimero-node-1"
  file_path: "res/kv_store.wasm" # Path to file on local filesystem
  context_id: "{{context_id}}" # Optional: associate with context
  outputs:
    blob_id: "wasm_blob_id" # Capture the blob ID
    size: "wasm_blob_size" # Capture the blob size

# Use the blob ID in contract calls
- name: "Register Blob in Contract"
  type: "call"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  method: "register_blob"
  args:
    blob_id: "{{wasm_blob_id}}" # Use real blob ID from upload
    size: "{{wasm_blob_size}}" # Use real size from upload
```

**Features:**

- Upload files from local filesystem to blob storage
- Capture `blob_id` and `size` for use in subsequent steps
- Optional context association via `context_id`
- Full binary file support
- Automatic error handling and validation

### Dynamic Variables

Workflows support dynamic variable substitution using `{{variable_name}}` syntax.

#### Variable Sources

- **Step Outputs**: Variables exported by previous steps
- **Workflow Context**: Global workflow variables
- **Environment**: System environment variables

#### Embedded Variables

Variables can be embedded within strings:

```yaml
args:
  key: "user_{{user_id}}_data_{{iteration}}"
```

#### Variable Resolution

- Variables are resolved at execution time
- Missing variables cause workflow failures
- Use `outputs` sections to export variables for later use

### Output Configuration

Each step can export variables for use in subsequent steps:

```yaml
outputs:
  variableName: "export_name" # Maps API response field to export name
```

### Example Workflows

- **Basic Workflow**: `workflow-examples/workflow-example.yml` - Complete example with dynamic variables
- **Negative Testing**: `workflow-examples/workflow-negative-testing-example.yml` - Testing error scenarios with expected failures
- **Assertions**: `workflow-examples/workflow-assert-example.yml` - Assertion and JSON assertion examples
- **Fuzzy Load Testing**: `workflow-examples/workflow-fuzzy-kv-store.yml` - Long-running load test with randomized operations

### Export variables from execute (call) steps

Call-like steps (type: `call`) return a JSON payload. You can export fields from that payload to named variables via the `outputs` mapping, and then reference those variables in subsequent steps using `{{variable_name}}`.

Example (from `workflow-execute-variables-example.yml`):

```yaml
- name: Execute Get
  type: call
  node: calimero-node-2
  context_id: "{{ctx_id}}"
  executor_public_key: "{{member_key}}"
  method: get
  args:
    key: example_key
  outputs:
    # Simple field access
    read_value: result

    # Nested field access with automatic JSON parsing
    # If result contains { "output": "value" }, this extracts "value"
    nested_value: result.output

    # Deep nesting also works: result.data.user.name.first
    deeply_nested: result.data.user.name

- name: Echo Exported Value
  type: script
  target: local
  inline: |
    echo "Exported value is: {{read_value}}"
    echo "Nested value is: {{nested_value}}"
```

**Syntax options:**

1. **Simple dotted path (recommended)**: Use dot notation to access nested fields. The system automatically parses JSON strings at each level.

   ```yaml
   outputs:
     my_value: result.output # Simple nested access
     deep_value: result.data.user.name # Deep nesting
     array_item: items.0.id # Array indexing
   ```

2. **Dict-based syntax (legacy, still supported)**: For backward compatibility, you can use the explicit dict form:
   ```yaml
   outputs:
     my_value:
       field: result # top-level field name to read from
       json: true # parse JSON if the field is a JSON string
       path: output # dotted path inside the parsed JSON
   ```

Notes:

- The `outputs` keys (e.g., `read_value`) become variables you can interpolate later as `{{read_value}}`.
- For more advanced mappings (including per-node variable names), see `workflow-custom-outputs-example.yml`.

### Running scripts in workflows (image, nodes, local) and passing args

The `script` step can execute a script in three ways:

- `target: image` runs the script inside a temporary container created from the node image (before nodes are started)
- `target: nodes` copies and runs the script inside each running Calimero node container
- `target: local` runs the script on your host machine via `/bin/sh`

You can also pass arguments and reference exported variables using placeholders. Arguments are resolved before execution.

Example:

```yaml
- name: Echo Exported Value
  type: script
  target: local # or "nodes" / "image"
  script: ./workflow-examples/scripts/echo-exported-value.sh
  args:
    - "{{read_value}}" # placeholder resolved from previous step outputs
```

Notes:

- The `script` field must be only the path to the script; pass parameters via the `args:` list.
- Placeholders in `args` are resolved using previously exported variables and workflow results.
- For container targets, the script is copied into the container and executed with `/bin/sh`.

### Assertion Steps

#### Assert (type: `assert`)

Statement-based assertions against exported variables and literals.

Supported forms:

- `is_set(A)` / `is_empty(A)`
- `contains(A, B)` / `not_contains(A, B)`
- `regex(A, PATTERN)`
- Comparisons: `A == B`, `A != B`, `A >= B`, `A > B`, `A <= B`, `A < B`
- Equality helpers: `equal(A, B)`, `equals(A, B)`, `not_equal(A, B)`, `not_equals(A, B)`

Placeholders like `{{var}}` are resolved before evaluation.

Example:

```yaml
- name: Assert exported variables
  type: assert
  statements:
    - "is_set({{context_id}})"
    - "{{count}} >= 1"
    - "contains({{get_result}}, 'hello')"
    - "regex({{value}}, '^abc')"
    - "equal({{a}}, {{b}})"
```

#### JSON Assert (type: `json_assert`)

Compare JSON-like values (Python dict/list or JSON strings).

Supported forms:

- `json_equal(A, B)` / `equal(A, B)`
- `json_subset(A, B)` / `subset(A, B)` (B must be subset of A)

Example:

```yaml
- name: Assert JSON equality of get_result
  type: json_assert
  statements:
    - "json_equal({{get_result}}, {'output': 'assert_value'})"
```

---

## 🎯 Local Blockchain Environments

Merobox offers two ways to run isolated tests without connecting to public networks. **These options are mutually exclusive.**

1. **Local NEAR Sandbox** (`--near-devnet`)
A real, ephemeral NEAR blockchain instance running locally.
- **Best for:** Full E2E testing and contract logic verification.
- **Behavior:** Executes actual WASM smart contracts and state transitions.

2. **Mock Relayer** (`--mock-relayer`)
A lightweight service that mimics the Relayer API.
- **Best for:** Fast connectivity checks and node startup validation.
- **Behavior:** Returns successful responses without executing real logic.

> **❌ Restriction**: You cannot use `--mock-relayer` and `--near-devnet` simultaneously. The workflow will fail if both are enabled.

### Local NEAR Sandbox

Merobox allows you to run workflows against a local ephemeral NEAR blockchain (Sandbox) instead of the public Testnet.
This enables faster E2E testing without needing testnet tokens or RPC access.

#### Requirements
You need the compiled WebAssembly (`.wasm`) files for the Calimero context contracts:
1. `calimero_context_config_near.wasm`
2. `calimero_context_proxy_near.wasm`

#### How to Run
Use the `--near-devnet` flag and point to your contracts directory:

```bash
merobox bootstrap run workflows/my-test.yml \
  --near-devnet \
  --contracts-dir ./path/to/wasm/files
```

**What happens during the run:**
1. **Sandbox Start**: Merobox downloads and starts `near-sandbox` locally on port 3030.
2. **Contract Deployment**: It creates a root account (`calimero.test.near`) and deploys the registry contracts.
3. **Node Configuration**: It generates funded NEAR accounts for every node in your workflow (e.g., `node-1.test.near`).
4. **Config Injection**: It overrides the node's `config.toml` to point to the local sandbox RPC (`http://host.docker.internal:3030` for Docker nodes).
5. **Cleanup**: The sandbox and all chain data are destroyed when the workflow finishes.

---

## 🔧 API Reference

### Command Overview

```bash
merobox [OPTIONS] COMMAND [ARGS]...
```

### Global Options

- `--version`: Show version and exit
- `--help`: Show help message and exit

### Core Commands

#### `merobox run`

Start Calimero nodes.

```bash
merobox run [OPTIONS]
```

**Options:**

- `--count INTEGER`: Number of nodes to start (default: 1)
- `--prefix TEXT`: Node name prefix (default: "calimero-node")
- `--restart`: Restart existing nodes
- `--image TEXT`: Custom Docker image to use
- `--force-pull`: Force pull Docker image even if it exists locally
- `--auth-service`: Enable authentication service with Traefik proxy
- `--auth-image TEXT`: Custom Docker image for the auth service (default: ghcr.io/calimero-network/mero-auth:edge)
- `--log-level TEXT`: Set the RUST_LOG level for Calimero nodes (default: debug). Supports complex patterns like 'info,module::path=debug'
- `--rust-backtrace TEXT`: Set the RUST_BACKTRACE level for Calimero nodes (default: 0).
- `--help`: Show help message

#### `merobox stop`

Stop Calimero nodes.

```bash
merobox stop [OPTIONS]
```

**Options:**

- `--all`: Stop all running nodes and auth service stack
- `--auth-service`: Stop auth service stack only (Traefik + Auth)
- `--prefix TEXT`: Stop nodes with specific prefix
- `--help`: Show help message

#### `merobox list`

List running Calimero nodes.

```bash
merobox list [OPTIONS]
```

**Options:**

- `--help`: Show help message

#### `merobox health`

Check health status of nodes.

```bash
merobox health [OPTIONS]
```

**Options:**

- `--help`: Show help message

#### `merobox logs`

View node logs.

```bash
merobox logs [OPTIONS] NODE_NAME
```

**Options:**

- `--follow`: Follow log output
- `--help`: Show help message

#### `merobox bootstrap`

Execute workflows and validate configurations.

```bash
merobox bootstrap [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**

- `run <config_file>`: Execute a workflow
- `validate <config_file>`: Validate workflow configuration
- `create-sample`: Create a sample workflow file

**Run Command Options:**

- `--auth-service`: Enable authentication service with Traefik proxy
- `--auth-image TEXT`: Custom Docker image for the auth service (default: ghcr.io/calimero-network/mero-auth:edge)
- `--near-devnet`: Spin up a local NEAR sandbox and configure nodes to use it.
- `--contracts-dir PATH`: Directory containing Near Context Config and Near Context Proxy contracts (required if using `--near-devnet`).
- `--log-level TEXT`: Set the RUST_LOG level for Calimero nodes (default: debug). Supports complex patterns like 'info,module::path=debug'
- `--rust-backtrace TEXT`: Set the RUST_BACKTRACE level for Calimero nodes (default: 0).
- `--verbose, -v`: Enable verbose output
- `--help`: Show help message

#### `merobox install`

Install applications on nodes.

```bash
merobox install [OPTIONS] NODE_NAME PATH_OR_URL
```

**Options:**

- `--dev`: Development mode installation
- `--help`: Show help message

#### `merobox context`

Manage blockchain contexts.

```bash
merobox context [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**

- `create`: Create a new context
- `list`: List contexts
- `show`: Show context details

#### `merobox identity`

Manage cryptographic identities.

```bash
merobox identity [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**

- `generate`: Generate new identity
- `list`: List identities
- `show`: Show identity details

#### `merobox call`

Execute smart contract functions.

```bash
merobox call [OPTIONS] NODE_NAME CONTEXT_ID METHOD [ARGS]...
```

**Options:**

- `--executor-key TEXT`: Executor public key
- `--exec-type TEXT`: Execution type
- `--help`: Show help message

#### `merobox join`

Join blockchain contexts.

```bash
merobox join [OPTIONS] NODE_NAME CONTEXT_ID INVITEE_ID INVITATION
```

**Options:**

- `--help`: Show help message

#### `merobox nuke`

Remove all node data and containers .

```bash
merobox nuke [OPTIONS]
```

**Options:**

- `--dry-run`: Show what would be deleted without actually deleting
- `--force, -f`: Force deletion without confirmation prompt
- `--verbose, -v`: Show verbose output
- `--prefix TEXT`: Filter nodes by prefix (e.g., 'calimero-node-' or 'test-node-')
- `--help`: Show help message

### Configuration Files

#### Workflow Configuration

Workflows are defined in YAML files with the following structure:

```yaml
name: "Workflow Name"

# Data cleanup options
nuke_on_start: false # Nuke all data before starting workflow
nuke_on_end: false # Nuke all data after completing workflow

# Image management
force_pull_image: false # Force pull Docker images even if they exist locally

# Node lifecycle
restart: false # Restart nodes at beginning
stop_all_nodes: true # Stop nodes after completion

# Network configuration
bootstrap_nodes: # Custom bootstrap nodes for DHT connectivity
  - "/ip4/x.x.x.x/udp/4001/quic-v1/p2p/<peer_id>"
  - "/ip4/x.x.x.x/tcp/4001/p2p/<peer_id>"

nodes:
  count: 2
  prefix: "node-name"
  # ... node configuration

steps:
  - name: "Step Name"
    type: "step_type"
    # ... step-specific configuration
```

**Configuration Options:**

- `nuke_on_start`: When `true`, performs complete data cleanup (containers + data) before workflow starts. Ensures clean slate.
- `nuke_on_end`: When `true`, performs complete data cleanup after workflow completes. Useful for CI/CD and testing.
- `force_pull_image`: When set to `true`, forces Docker to pull fresh images from registries, even if they exist locally. Useful for ensuring latest versions or during development.
- `auth_service`: When set to `true`, enables authentication service integration with Traefik proxy. Nodes will be configured with authentication middleware and proper routing.
- `config_path`: Specify custom `config.toml` path for nodes. Supports both shared config for all nodes and per-node overrides. Skips node initialization when custom config is provided. See [Custom Config Path](#custom-config-path) for details.
- `near_devnet`: When set to `true`, spins up a local NEAR Sandbox that is used for context management during the workflow running.
- `contracts_dir`: Path to directory containing required WASM contracts (required when `near_devnet` is true).
- `bootstrap_nodes`: List of multiaddr strings for DHT bootstrap nodes. When specified, nodes will connect to these peers for network discovery. Works independently of `e2e_mode`. Each multiaddr must end with a `/p2p/<peer_id>` component (e.g., `"/ip4/63.181.86.34/tcp/4001/p2p/12D3KooW..."`).

### Docker Image Management

Merobox provides automatic Docker image management to ensure your workflows always have the required images:

#### **Automatic Image Pulling**

- **Remote Detection**: Automatically detects when images are from remote registries
- **Smart Pulling**: Only pulls images that aren't available locally
- **Progress Display**: Shows real-time pull progress and status

#### **Force Pull Options**

1. **CLI Flag**: Use `--force-pull` with the `run` command for individual operations

   ```bash
   merobox run --image ghcr.io/calimero-network/merod:edge --force-pull
   ```

2. **Workflow Configuration**: Set `force_pull_image: true` in your workflow YAML
   ````yaml
   name: "My Workflow"
   image: ghcr.io/calimero-network/merod:edge
   force_pull_image: true # Will force pull all images
   nodes:
     count: 2
     # ... other node configuration   ```
   ````

#### **Use Cases**

- **Development**: Always get latest images during development
- **Testing**: Ensure consistent image versions across environments
- **CI/CD**: Force fresh pulls in automated workflows
- **Production**: Update images without manual intervention

#### Environment Variables

- `CALIMERO_IMAGE`: Docker image for Calimero nodes
- `DOCKER_HOST`: Docker daemon connection string
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `RUST_BACKTRACE`: RUST_BACKTRACE level (0, 1, full)
- `CALIMERO_AUTH_FRONTEND_FETCH`: Set to `0` to use cached auth frontend (default is `1` for fresh fetch)
- `CALIMERO_WEBUI_FETCH`: Set to `0` to use cached WebUI frontend (default is `1` for fresh fetch)

### Custom Config Path

Merobox allows you to specify custom `config.toml` files for workflow nodes, enabling you to reuse existing node configurations without relying on Docker image initialization.

#### **Usage in Workflows**

You can specify a custom config path at two levels:

**Shared config for all nodes:**

```yaml
nodes:
  config_path: ./custom-config.toml  # All nodes use this config
  calimero-node-1:
    port: 2428
    rpc_port: 2528
  calimero-node-2:
    port: 2429
    rpc_port: 2529
```

**Per-node config override:**

```yaml
nodes:
  config_path: ./default-config.toml  # Default for all nodes
  calimero-node-1:
    port: 2428
    rpc_port: 2528
    # Uses default config
  calimero-node-2:
    port: 2429
    rpc_port: 2529
    config_path: ./special-config.toml  # Override for this node
```

#### **Features**

- **Shared or Per-Node**: Specify a default config for all nodes or override per-node
- **Skip Initialization**: When custom config is provided, node initialization is skipped
- **Docker & Binary Mode**: Works with both Docker containers and native binary (`--no-docker`) mode
- **Path Resolution**: Supports both absolute and relative paths
- **Validation**: Validates config file existence before attempting to use it

#### **Example**

See `workflow-examples/workflow-custom-config-example.yml` and `workflow-examples/custom-config.toml` for a complete example.

**Note:** Custom config path is not supported with `count` mode (bulk node creation).

### Log Level Configuration

Merobox provides flexible logging configuration for Calimero nodes through both CLI flags and workflow variables.

#### **CLI Usage**

```bash
# Use different log levels
merobox run --log-level info
merobox run --log-level warn
merobox run --log-level error
merobox run --log-level trace

# Use complex RUST_LOG patterns for specific module debugging
merobox run --log-level "info,calimero_context::handlers::execute=debug,calimero_node::handlers::network_event=debug"
merobox run --log-level "warn,calimero_server::ws=trace"

# Bootstrap workflows with custom log level
merobox bootstrap run workflow.yml --log-level "info,calimero_context::handlers::execute=debug"
```

#### **Workflow Configuration**

```yaml
name: "My Workflow"
log_level: "info,calimero_context::handlers::execute=debug" # Set log level for all nodes in this workflow
nodes:
  count: 2
  # ... other node configuration
```

#### **Available Log Levels**

- `error`: Only error messages (least verbose)
- `warn`: Warning and error messages
- `info`: Informational, warning, and error messages
- `debug`: Debug, info, warning, and error messages (default)
- `trace`: All messages including trace-level details (most verbose)

#### **Complex RUST_LOG Patterns**

RUST_LOG supports sophisticated logging configuration with module-specific levels:

```bash
# Set global level to info, but enable debug for specific modules
merobox run --log-level "info,calimero_context::handlers::execute=debug,calimero_node::handlers::network_event=debug"

# Set global level to warn, but enable trace for WebSocket handling
merobox run --log-level "warn,calimero_server::ws=trace"

# Multiple module-specific levels
merobox run --log-level "info,calimero_context::handlers::execute=debug,calimero_node::handlers::network_event=debug,calimero_server::ws=debug"
```

**Pattern Syntax:**

- `global_level` - Sets the default log level
- `module::path=level` - Sets specific level for a module path
- Multiple patterns separated by commas
- Use quotes to prevent shell interpretation of special characters

#### **Usage Examples**

```bash
# Production setup with minimal logging
merobox run --count 3 --log-level warn

# Development with maximum verbosity
merobox run --count 2 --log-level trace

# Workflow with custom log level
merobox bootstrap run production-workflow.yml --log-level info
```

### Auth Service Integration

Merobox supports integration with Calimero's authentication service using Traefik as a reverse proxy. When enabled, nodes are automatically configured with:

#### **Authentication Features**

- **Protected API Endpoints**: JSON-RPC and admin API routes require authentication
- **Public Admin Dashboard**: Admin dashboard remains publicly accessible
- **WebSocket Protection**: WebSocket connections are also authenticated
- **Automatic Routing**: Traefik handles routing to node-specific subdomains

#### **Network Configuration**

- **Docker Networks**: Automatically creates `calimero_web` and `calimero_internal` networks
- **Traefik Labels**: Adds proper routing labels for each node
- **CORS Support**: Configured CORS middleware for web access

#### **Auth Frontend Management**

Merobox provides flexible options for managing auth service frontend updates:

- **Fresh Frontend (Default)**: By default, auth service fetches fresh frontend resources (`CALIMERO_AUTH_FRONTEND_FETCH=1`)
- **Cached Mode**: Use `--auth-use-cached` flag or set `CALIMERO_AUTH_FRONTEND_FETCH=0` to use cached auth frontend
- **Custom Images**: Specify custom auth images with `--auth-image` flag or `auth_image` in workflow config
- **Workflow Config**: Set `auth_use_cached: true` in workflow YAML to use cached auth frontend

**Environment Variable Usage:**

```bash
# Use cached auth frontend for all auth service operations
export CALIMERO_AUTH_FRONTEND_FETCH=0
merobox run --auth-service

# Or set for single command
CALIMERO_AUTH_FRONTEND_FETCH=0 merobox run --auth-service
```

#### **Node WebUI Frontend Management**

Merobox provides flexible options for managing node WebUI frontend updates:

- **Fresh Frontend (Default)**: By default, nodes fetch fresh WebUI frontend resources (`CALIMERO_WEBUI_FETCH=1`)
- **Cached Mode**: Use `--webui-use-cached` flag or set `CALIMERO_WEBUI_FETCH=0` to use cached WebUI frontend
- **Custom Images**: Specify custom node images with `--image` flag or `image` in workflow config
- **Workflow Config**: Set `webui_use_cached: true` in workflow YAML to use cached WebUI frontend

**Environment Variable Usage:**

```bash
# Use cached WebUI frontend for all node operations
export CALIMERO_WEBUI_FETCH=0
merobox run --count 2

# Or set for single command
CALIMERO_WEBUI_FETCH=0 merobox run --count 2
```

#### **Usage Examples**

**CLI Usage:**

```bash
# Start nodes with auth service
merobox run --count 2 --auth-service

# Start nodes with custom auth image
merobox run --count 2 --auth-service --auth-image ghcr.io/calimero-network/mero-auth:latest

# Use cached auth frontend (instead of default fresh fetch)
merobox run --count 2 --auth-service --auth-use-cached

# Use cached WebUI frontend for nodes (instead of default fresh fetch)
merobox run --count 2 --webui-use-cached

# Use cached mode for both auth and WebUI
merobox run --count 2 --auth-service --auth-use-cached --webui-use-cached

# Run workflow with auth service
merobox bootstrap run workflow.yml --auth-service

# Run workflow with custom auth image
merobox bootstrap run workflow.yml --auth-service --auth-image ghcr.io/calimero-network/mero-auth:latest

# Run workflow with cached auth frontend
merobox bootstrap run workflow.yml --auth-service --auth-use-cached

# Run workflow with cached WebUI frontend
merobox bootstrap run workflow.yml --webui-use-cached

# Run workflow with both auth and WebUI in cached mode
merobox bootstrap run workflow.yml --auth-service --auth-use-cached --webui-use-cached

# Stop auth service stack
merobox stop --auth-service
```

**Workflow Configuration:**

```yaml
name: "Frontend Management Workflow"
# Auth service configuration (fresh frontend is default, use cached if needed)
auth_service: true # Enable auth service for this workflow
auth_image: "ghcr.io/calimero-network/mero-auth:edge" # Custom auth image
auth_use_cached: true # Use cached auth frontend instead of fresh (optional)

# Node configuration (fresh WebUI is default, use cached if needed)
image: "ghcr.io/calimero-network/merod:edge" # Custom node image
webui_use_cached: true # Use cached WebUI frontend instead of fresh (optional)

nodes:
  count: 2
  prefix: "calimero-node"
steps:
  # ... your workflow steps
```

**Access Patterns:**

- Node 1 API: `http://calimero-node-1.127.0.0.1.nip.io/jsonrpc` (protected)
- Node 1 Dashboard: `http://calimero-node-1.127.0.0.1.nip.io/admin-dashboard` (public)
- Auth Service: `http://localhost/auth/` (authentication endpoints)

#### **Automatic Service Management**

When auth service is enabled, Merobox automatically:

1. **Starts Traefik Proxy**: Automatically pulls and starts `traefik:v2.10` container
2. **Starts Auth Service**: Automatically pulls and starts `ghcr.io/calimero-network/calimero-auth:latest` container
3. **Creates Docker Networks**: Sets up `calimero_web` and `calimero_internal` networks
4. **Configures Node Labels**: Adds proper Traefik routing labels to node containers
5. **Sets up Authentication**: Configures forward authentication middleware
6. **Enables CORS**: Configures CORS for web access

**Service Management:**

- **Start**: Services are started automatically when `--auth-service` flag is used
- **Stop**: Use `merobox stop --auth-service` to stop Traefik and Auth service
- **Status Check**: Services are checked and reused if already running

---

## 🛠️ Development Guide

### Testing with Merobox

Merobox can be used as a lightweight test harness for your Python projects. Use the built-in helpers in `merobox.testing` to spin up ephemeral Calimero nodes for integration tests and tear them down automatically.

#### Basic Cluster Management

**Context manager:**

```python
from merobox.testing import cluster

with cluster(count=2, prefix="ci", image="ghcr.io/calimero-network/merod:edge") as env:
    # env["nodes"] -> ["ci-1", "ci-2"]
    # env["endpoints"]["ci-1"] -> http://localhost:<rpc_port>
    ...  # call your code against the endpoints
```

**Pytest fixture:**

```python
# conftest.py
from merobox.testing import pytest_cluster

merobox_cluster = pytest_cluster(count=2, scope="session")

# test_example.py
def test_something(merobox_cluster):
    endpoints = merobox_cluster["endpoints"]
    assert len(endpoints) == 2
```

#### Workflow-based Pretest Setup

For more complex test scenarios, you can run entire Merobox workflows as pretest setup:

**Context manager:**

```python
from merobox.testing import workflow

with workflow("workflow-examples/workflow-example.yml", prefix="pretest") as env:
    # env["workflow_result"] -> True/False (workflow execution success)
    # env["nodes"] -> List of nodes created by the workflow
    # env["endpoints"] -> RPC endpoints for each node
    # env["manager"] -> DockerManager instance

    # Your test logic here
    # The workflow environment is automatically cleaned up on exit
```

**Pytest fixture:**

```python
# conftest.py
from merobox.testing import pytest_workflow

merobox_workflow = pytest_workflow(
    workflow_path="workflow-examples/workflow-example.yml",
    prefix="pretest",
    scope="session"
)

# test_example.py
def test_with_workflow_setup(merobox_workflow):
    workflow_result = merobox_workflow["workflow_result"]
    assert workflow_result is True

    nodes = merobox_workflow["nodes"]
    endpoints = merobox_workflow["endpoints"]
    # ... your test logic
```

**Options for workflow testing:**

- `workflow_path`: Path to the workflow YAML file
- `prefix`: Node name prefix filter
- `image`: Custom Docker image
- `chain_id`: Blockchain chain ID
- `wait_for_ready`: Whether to wait for nodes to be ready
- `scope`: Pytest fixture scope (function, class, module, session)

See `testing-examples/` for runnable examples including workflow pretest setup.

### Environment Setup

#### Prerequisites

- Python 3.8+
- Docker 20.10+
- Git

#### Local Development

```bash
# Clone repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

#### Development Dependencies

```bash
pip install -r requirements.txt
```

### Project Structure

```
merobox/
├── merobox/                    # Main package
│   ├── __init__.py            # Package initialization
│   ├── cli.py                 # CLI entry point
│   └── commands/              # Command implementations
│       ├── __init__.py        # Commands package
│       ├── manager.py         # Docker node management
│       ├── run.py             # Node startup
│       ├── stop.py            # Node shutdown
│       ├── list.py            # Node listing
│       ├── health.py          # Health checking
│       ├── logs.py            # Log viewing
│       ├── install.py         # Application installation
│       ├── context.py         # Context management
│       ├── identity.py        # Identity management
│       ├── call.py            # Function execution
│       ├── join.py            # Context joining
│       ├── nuke.py            # Data cleanup
│       ├── utils.py           # Utility functions
│       └── bootstrap/         # Workflow orchestration
│           ├── __init__.py
│           ├── bootstrap.py   # Main bootstrap command
│           ├── config.py      # Configuration loading
│           ├── run/           # Workflow execution
│           │   ├── __init__.py
│           │   ├── executor.py # Workflow executor
│           │   └── run.py     # Execution logic
│           ├── steps/         # Step implementations
│           │   ├── __init__.py
│           │   ├── base.py    # Base step class
│           │   ├── install.py # Install step
│           │   ├── context.py # Context step
│           │   ├── identity.py # Identity step
│           │   ├── execute.py # Execute step
│           │   ├── join.py    # Join step
│           │   ├── wait.py    # Wait step
│           │   ├── repeat.py  # Repeat step
│           │   └── script.py  # Script step
│           └── validate/      # Validation logic
│               ├── __init__.py
│               └── validator.py
├── workflow-examples/          # Example workflows
├── requirements.txt            # Python dependencies
├── setup.py                   # Package configuration
├── Makefile                   # Build automation
├── README.md                  # This file
└── LICENSE                    # MIT License
```

### Building and Testing

#### Build Commands

```bash
# Show all available commands
make help

# Build package
make build

# Check package
make check

# Install in development mode
make install

# Format code
make format

# Check formatting
make format-check
```

#### Testing

```bash
# Run tests (when implemented)
make test

# Run specific test file
python -m pytest tests/test_specific.py
```

#### Code Quality

```bash
# Format code with Black
make format

# Check formatting
make format-check

# Lint code (when implemented)
make lint
```

### Adding New Commands

1. Create command file in `merobox/commands/`
2. Implement Click command function
3. Add import to `merobox/commands/__init__.py`
4. Update `__all__` list
5. Test with `python3 merobox/cli.py --help`

### Adding New Step Types

1. Create step file in `merobox/commands/bootstrap/steps/`
2. Inherit from `BaseStep`
3. Implement required methods:
   - `_get_required_fields()`
   - `_validate_field_types()`
   - `execute()`
4. Add step type mapping in executor
5. Update validation logic

### Release Process

#### Version Management

- Update version in `merobox/__init__.py`
- Update version in `merobox/cli.py`
- Update version in `setup.py`
- Add entry to `CHANGELOG.md`

#### Publishing

```bash
# Build and check
make check

# Test publish to TestPyPI
make test-publish

# Publish to PyPI
make publish
```

#### Release Checklist

- [ ] All tests pass
- [ ] Documentation updated
- [ ] Version bumped
- [ ] Changelog updated
- [ ] Package builds successfully
- [ ] Package validates with twine
- [ ] Published to PyPI

---

## ❓ Troubleshooting

### Common Issues

#### Node Startup Problems

**Issue**: Nodes fail to start

```bash
Error: Failed to start Calimero node
```

**Solutions**:

1. Check Docker is running: `docker ps`
2. Verify port availability: `netstat -tulpn | grep :2528`
3. Check Docker permissions: `docker run hello-world`
4. Clean up existing containers: `merobox nuke`

**Issue**: Port conflicts

```bash
Error: Port 2528 already in use
```

**Solutions**:

1. Stop conflicting services: `lsof -ti:2528 | xargs kill`
2. Use different ports: `merobox run --count 1`
3. Clean up: `merobox stop --all`

#### Workflow Execution Issues

**Issue**: Dynamic variable resolution fails

```bash
Error: Variable '{{missing_var}}' not found
```

**Solutions**:

1. Check variable names in workflow
2. Verify previous steps export variables
3. Use `merobox bootstrap validate` to check configuration
4. Check variable naming consistency

**Issue**: Step validation fails

```bash
Error: Required field 'node' missing
```

**Solutions**:

1. Validate workflow: `merobox bootstrap validate workflow.yml`
2. Check step configuration
3. Verify required fields are present
4. Check field types and values

**Issue**: API calls fail

```bash
Error: API request failed
```

**Solutions**:

1. Check node health: `merobox health`
2. Verify node is ready: `merobox list`
3. Check network connectivity
4. Verify API endpoints

#### Auth Service Issues

**Issue**: Cannot access node via nip.io URL

```bash
ERR_CONNECTION_TIMED_OUT at http://node1.127.0.0.1.nip.io
```

**Solutions**:

1. Check if auth services are running: `docker ps | grep -E "(proxy|auth)"`
2. Verify DNS resolution: `nslookup node1.127.0.0.1.nip.io`
3. Check Traefik dashboard: `http://localhost:8080/dashboard/`
4. Restart auth services: `merobox stop --auth-service && merobox run --auth-service`

**Issue**: 404 errors on auth URLs

```bash
404 Not Found at http://node1.127.0.0.1.nip.io/auth/login
```

**Solutions**:

1. Verify auth container is running: `docker logs auth`
2. Check Traefik routing: `curl http://localhost:8080/api/http/routers`
3. Restart the node: `merobox stop node-name && merobox run --auth-service`

**Issue**: Network connection problems

```bash
Warning: Could not connect to auth networks
```

**Solutions**:

1. Check Docker networks: `docker network ls | grep calimero`
2. Recreate networks: `merobox stop --all && merobox run --auth-service`
3. Check Docker daemon: `docker system info`

#### Docker Issues

**Issue**: Container creation fails

```bash
Error: Failed to create container
```

**Solutions**:

1. Check Docker daemon: `docker info`
2. Verify image exists: `docker images calimero/calimero`
3. Check disk space: `df -h`
4. Restart Docker: `sudo systemctl restart docker`

**Issue**: Container networking problems

```bash
Error: Network connection failed
```

**Solutions**:

1. Check Docker network: `docker network ls`
2. Verify container networking: `docker inspect <container>`
3. Check firewall settings
4. Restart Docker networking

#### Performance Issues

**Issue**: Slow workflow execution

```bash
Workflow taking longer than expected
```

**Solutions**:

1. Check node resources: `docker stats`
2. Monitor system resources: `htop`, `iotop`
3. Optimize workflow steps
4. Use appropriate wait times

**Issue**: High memory usage

```bash
Container using excessive memory
```

**Solutions**:

1. Check memory limits: `docker stats`
2. Monitor memory usage: `free -h`
3. Restart nodes if needed
4. Check for memory leaks

### Debugging

#### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
merobox bootstrap run workflow.yml
```

#### Verbose Output

```bash
merobox bootstrap run --verbose workflow.yml
```

#### Check Node Logs

```bash
merobox logs <node_name> --follow
```

#### Inspect Containers

```bash
docker exec -it <container_name> /bin/sh
docker inspect <container_name>
```

#### Network Diagnostics

```bash
# Check container networking
docker network inspect bridge

# Test connectivity
docker exec <container> ping <target>

# Check port binding
netstat -tulpn | grep :2528
```

### Getting Help

1. **Check Documentation**: Review relevant sections above
2. **Validate Workflows**: Use `merobox bootstrap validate`
3. **Check Logs**: Review node and application logs
4. **Community Support**: [GitHub Issues](https://github.com/calimero-network/merobox/issues)
5. **Command Help**: `merobox --help` or `merobox <command> --help`

---

## 🏗️ Project Structure

```
merobox/
├── merobox/                    # Main package
│   ├── cli.py                 # CLI entry point
│   └── commands/              # Command implementations
│       ├── bootstrap/         # Workflow orchestration
│       ├── run.py             # Node management
│       ├── call.py            # Function execution
│       └── ...                # Other commands
├── workflow-examples/          # Example workflows
├── Makefile                   # Build automation
└── README.md                  # This comprehensive documentation
```

## 📋 Requirements

- **Python**: 3.9 - 3.11
  - **Note**: Python 3.12+ is currently not supported. The Near Sandbox feature uses `py-near` dependency relies
  on `ed25519` package (that uses `SafeConfigParser`, which was removed in Python 3.12) that is limited by Python 3.11.
  Please use Python 3.11 or older.
- **Docker**: 20.10+ for Calimero nodes
- **OS**: Linux, macOS, Windows
  - **Note**: Linux/macOS required for near-sandbox local devnet. Windows platform is not currently supported (you may try using WSL).

## 🚀 Releases & Publishing

### Automated Release Process

Merobox uses a fully automated release pipeline. When you bump the version, everything else happens automatically!

#### How to Release

```bash
# 1. Update version in ONE place only
vim merobox/__init__.py  # Change __version__ = "0.1.28"

# 2. Commit and push to master
git add merobox/__init__.py
git commit -m "chore: bump version to 0.1.28"
git push origin master

# 3. That's it! The automation handles:
#    ✓ Creates git tag (v0.1.28)
#    ✓ Builds binaries for all platforms
#    ✓ Creates GitHub release
#    ✓ Publishes to PyPI
```

#### What Happens Automatically

1. **Auto-Tagging** (~ 5 seconds)

   - Detects version change in `__init__.py`
   - Creates and pushes tag `vX.Y.Z`
   - Comments on commit with status

2. **Build Binaries** (~ 5-10 minutes)

   - macOS x64 & arm64
   - Linux x64 & arm64
   - Generates SHA256 checksums

3. **Create Release** (~ 30 seconds)

   - Publishes GitHub release with binaries
   - Auto-generates release notes

4. **Publish to PyPI** (~ 1 minute)
   - Builds Python package (sdist + wheel)
   - Publishes to PyPI
   - Publishes to TestPyPI (optional)

#### Version Management

- **Single Source of Truth**: `merobox/__init__.py`
- **Dynamic Reading**: `pyproject.toml` reads version from `__init__.py` automatically
- **No Duplication**: Update version in one place only!

#### Workflow Pipeline

```
Version Bump → Auto-Tag → Build Binaries → GitHub Release → PyPI
   (manual)    (automated)   (automated)     (automated)   (automated)
```

#### Required Secrets

Configure these in GitHub repository settings:

- `PYPI_API_TOKEN` - PyPI publishing token (required)
- `TEST_PYPI_API_TOKEN` - TestPyPI token (optional)

#### Manual Publishing (Backup)

If you need to publish manually:

```bash
# Build package
make clean
make build

# Check package
make check-build

# Publish to PyPI
make publish

# Or publish to TestPyPI
make test-publish
```

#### Monitoring Releases

- **GitHub Actions**: https://github.com/calimero-network/merobox/actions
- **PyPI Releases**: https://pypi.org/project/merobox/
- **GitHub Releases**: https://github.com/calimero-network/merobox/releases

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

See the [Development Guide](#️-development-guide) section above for detailed contribution instructions.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: This comprehensive README
- **Examples**: See `workflow-examples/` directory
- **Issues**: [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- **Help**: `merobox --help` for command help

## 🔗 Quick Links

- **[🚀 Quick Start](#-quick-start)**
- **[📖 Workflow Guide](#-workflow-guide)**
- **[🔧 API Reference](#-api-reference)**
- **[🛠️ Development Guide](#️-development-guide)**
- **[❓ Troubleshooting](#-troubleshooting)**
- **[Examples](workflow-examples/) directory**
- **[Source](https://github.com/calimero-network/merobox)**
