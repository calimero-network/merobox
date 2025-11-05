# Merobox - LLM Usage Guide

## What is Merobox?

Merobox is a Python CLI tool for managing Calimero nodes in Docker containers. It provides:
- Easy node lifecycle management (start, stop, list, health checks)
- Application installation and execution on nodes
- Identity and context management
- Automated workflow execution via YAML files
- Multi-node orchestration and testing

## Installation

**Option 1: Using Homebrew**

```bash
# Install via Homebrew
brew install merobox

# Verify installation
merobox --version
```

**Option 2: Using pipx (recommended for non-Homebrew users)**

First, ensure you have pipx installed:

```bash
# Install pipx (if not already installed)
# On macOS/Linux
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# On Ubuntu/Debian
sudo apt install pipx
pipx ensurepath

# Restart your terminal after ensurepath
```

Then install merobox:

```bash
# Install from PyPI using pipx
pipx install merobox

# Verify installation
merobox --version
```

## Basic Commands

### Node Management

```bash
# Start a Calimero node
merobox run --name my-node

# Start with custom ports
merobox run --name my-node --server-port 2428 --swarm-port 2528

# List running nodes
merobox list

# Check node health
merobox health my-node

# View node logs
merobox logs my-node

# Follow logs in real-time
merobox logs my-node --follow

# Stop a node
merobox stop my-node

# Remove all node data (destructive!)
merobox nuke my-node
```

### Application Management

```bash
# Install a WASM application on a node
merobox install my-node /path/to/app.wasm

# Call a function on an installed application
merobox call my-node <app-id> <method> '{"arg": "value"}'
```

### Identity & Context Management

```bash
# Create a new identity
merobox identity create

# Create a new context
merobox context create my-node <context-config>

# Join a node to a context
merobox join my-node <context-id> <private-key>
```

## Workflow System

Merobox's most powerful feature is YAML-based workflow automation. Workflows allow you to:
- Orchestrate multiple nodes
- Automate complex testing scenarios
- Manage multi-step deployments
- Handle context creation and node joining

### Running Workflows

```bash
# Execute a workflow
merobox bootstrap run workflow.yml

# Validate a workflow without running
merobox bootstrap validate workflow.yml

# Create a sample workflow
merobox bootstrap create-sample
```

## Workflow YAML Structure

### Basic Workflow Example

```yaml
name: Simple Node Setup
steps:
  - type: install
    node_name: node-1
    wasm_path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: call
    node_name: node-1
    application_id: "{{app_id}}"
    method: "initialize"
    args:
      key: "value"
```

### Available Step Types

#### 1. Install Step
Install a WASM application on a node.

```yaml
- type: install
  node_name: node-1
  wasm_path: ./my-app.wasm
  outputs:
    app_id: "application_id"
```

#### 2. Context Step
Create a new context on a node.

```yaml
- type: context
  node_name: node-1
  application_id: "{{app_id}}"
  outputs:
    context_id: "context.context_id"
    context_seed: "context.seed"
```

#### 3. Identity Step
Create a new identity for a node.

```yaml
- type: identity
  outputs:
    private_key: "private_key"
    public_key: "public_key"
```

#### 4. Join Step
Join a node to a context.

```yaml
- type: join
  node_name: node-2
  context_id: "{{context_id}}"
  private_key: "{{private_key}}"
```

#### 5. Execute (Call) Step
Call a method on an application.

```yaml
- type: execute
  node_name: node-1
  context_id: "{{context_id}}"
  method: "set_value"
  args:
    key: "my_key"
    value: "my_value"
  executor_public_key: "{{public_key}}"
  outputs:
    result: "output"
```

#### 6. Wait Step
Pause execution for a specified duration.

```yaml
- type: wait
  seconds: 5
```

#### 7. Repeat Step
Loop through a sequence of steps multiple times.

```yaml
- type: repeat
  times: 3
  variable: iteration
  steps:
    - type: execute
      node_name: node-1
      method: "process_{{iteration}}"
  outputs:
    - key: "result_{{iteration}}"
      value: "output"
```

#### 8. Script Step
Execute shell scripts or commands.

```yaml
- type: script
  pre_script: "./scripts/setup.sh"
  post_script: "./scripts/cleanup.sh"
  steps:
    - type: wait
      seconds: 1
```

#### 9. Assertion Steps
Validate execution results.

```yaml
# Simple assertion
- type: assert
  node_name: node-1
  context_id: "{{context_id}}"
  method: "get_value"
  args:
    key: "my_key"
  expected_output: "my_value"

# JSON path assertion
- type: json_assert
  node_name: node-1
  context_id: "{{context_id}}"
  method: "get_data"
  json_path: "$.user.name"
  expected_value: "Alice"
```

#### 10. Proposals
Create and vote on proposals in a context.

```yaml
- type: propose
  node_name: node-1
  context_id: "{{context_id}}"
  author_id: "{{public_key}}"
  actions:
    - type: "ExternalFunctionCall"
      method: "update_config"
      args:
        setting: "enabled"
  outputs:
    proposal_id: "proposal_id"

- type: approve
  node_name: node-1
  context_id: "{{context_id}}"
  proposal_id: "{{proposal_id}}"
  approver_id: "{{public_key}}"
```

## Variable Substitution

Workflows support dynamic variable substitution using `{{variable_name}}` syntax.

### Variable Sources
1. **Step outputs**: Capture values from previous steps
2. **Environment variables**: `{{env.MY_VAR}}`
3. **Iteration variables**: In repeat loops `{{iteration}}`
4. **Embedded variables**: `key_{{iteration}}_suffix`

### Example with Variables

```yaml
steps:
  - type: identity
    outputs:
      admin_key: "private_key"
      admin_pub: "public_key"

  - type: install
    node_name: main-node
    wasm_path: ./app.wasm
    outputs:
      app: "application_id"

  - type: context
    node_name: main-node
    application_id: "{{app}}"
    outputs:
      ctx: "context.context_id"

  - type: execute
    node_name: main-node
    context_id: "{{ctx}}"
    method: "initialize"
    executor_public_key: "{{admin_pub}}"
```

## Multi-Node Workflows

Example: Create a network with 3 nodes sharing a context

```yaml
name: Multi-Node Network
steps:
  # Start nodes
  - type: install
    node_name: node-1
    wasm_path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: context
    node_name: node-1
    application_id: "{{app_id}}"
    outputs:
      context_id: "context.context_id"
      seed: "context.seed"

  # Create identities for joining nodes
  - type: identity
    outputs:
      node2_key: "private_key"

  - type: identity
    outputs:
      node3_key: "private_key"

  # Join additional nodes
  - type: join
    node_name: node-2
    context_id: "{{context_id}}"
    private_key: "{{node2_key}}"

  - type: join
    node_name: node-3
    context_id: "{{context_id}}"
    private_key: "{{node3_key}}"

  - type: wait
    seconds: 2

  # Verify all nodes can execute
  - type: execute
    node_name: node-2
    context_id: "{{context_id}}"
    method: "ping"
```

## Common Patterns

### Pattern 1: Simple Testing Setup

```yaml
name: Quick Test
steps:
  - type: install
    node_name: test-node
    wasm_path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: context
    node_name: test-node
    application_id: "{{app_id}}"
    outputs:
      ctx_id: "context.context_id"

  - type: identity
    outputs:
      pk: "private_key"
      pub: "public_key"

  - type: execute
    node_name: test-node
    context_id: "{{ctx_id}}"
    method: "test_function"
    executor_public_key: "{{pub}}"

  - type: assert
    node_name: test-node
    context_id: "{{ctx_id}}"
    method: "get_result"
    expected_output: "success"
```

### Pattern 2: Batch Operations with Repeat

```yaml
name: Batch Insert
steps:
  # ... setup steps ...

  - type: repeat
    times: 10
    variable: i
    steps:
      - type: execute
        node_name: node-1
        context_id: "{{context_id}}"
        method: "insert"
        args:
          key: "item_{{i}}"
          value: "data_{{i}}"
        executor_public_key: "{{pub_key}}"
    outputs:
      - key: "result_{{i}}"
        value: "output"
```

### Pattern 3: Cross-Node Communication

```yaml
name: Node Communication Test
steps:
  # Setup two nodes in same context
  - type: install
    node_name: sender
    wasm_path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: context
    node_name: sender
    application_id: "{{app_id}}"
    outputs:
      ctx: "context.context_id"

  - type: identity
    outputs:
      receiver_key: "private_key"

  - type: join
    node_name: receiver
    context_id: "{{ctx}}"
    private_key: "{{receiver_key}}"

  # Sender writes data
  - type: execute
    node_name: sender
    context_id: "{{ctx}}"
    method: "write_message"
    args:
      msg: "Hello from sender"

  - type: wait
    seconds: 1

  # Receiver reads data
  - type: assert
    node_name: receiver
    context_id: "{{ctx}}"
    method: "read_message"
    expected_output: "Hello from sender"
```

## Workflow Best Practices

### 1. Use Descriptive Output Variables
```yaml
# Good
outputs:
  main_app_id: "application_id"
  admin_context: "context.context_id"

# Less clear
outputs:
  x: "application_id"
  y: "context.context_id"
```

### 2. Add Wait Steps After Node Operations
```yaml
- type: join
  node_name: node-2
  context_id: "{{ctx}}"
  private_key: "{{key}}"

- type: wait
  seconds: 2  # Allow node to fully sync
```

### 3. Use Assertions to Validate State
```yaml
- type: execute
  node_name: node-1
  method: "set_value"
  args:
    key: "test"
    value: "123"

- type: assert
  node_name: node-1
  method: "get_value"
  args:
    key: "test"
  expected_output: "123"
```

### 4. Organize Complex Workflows with Scripts
```yaml
- type: script
  pre_script: "./setup-environment.sh"
  steps:
    # ... main workflow steps ...
  post_script: "./cleanup.sh"
```

## Troubleshooting

### Node Won't Start
```bash
# Check if ports are in use
merobox list

# Try different ports
merobox run --name my-node --server-port 3000 --swarm-port 3001

# Check Docker status
docker ps
```

### Application Installation Fails
```bash
# Verify WASM file exists
ls -lh /path/to/app.wasm

# Check node is running
merobox health my-node

# View detailed logs
merobox logs my-node --follow
```

### Workflow Execution Issues
```bash
# Validate workflow syntax first
merobox bootstrap validate workflow.yml

# Check variable substitution
# Look for {{variable}} in error messages

# Enable detailed output (if available)
# Add debug logging to workflow steps
```

### Common Workflow Errors

**Missing Variable**: Variable not defined in outputs
```yaml
# Fix: Ensure variable is captured
outputs:
  my_var: "path.to.value"
```

**Node Not Found**: Node name doesn't match running node
```bash
# Check running nodes
merobox list

# Ensure node names match exactly
```

**Context Sync Issues**: Nodes not synchronized
```yaml
# Add wait steps after join operations
- type: wait
  seconds: 2
```

## Advanced Features

### Environment Variables in Workflows
```yaml
- type: execute
  node_name: "{{env.NODE_NAME}}"
  context_id: "{{env.CONTEXT_ID}}"
  method: "process"
```

### JSON Path Assertions
```yaml
- type: json_assert
  node_name: node-1
  method: "get_user"
  json_path: "$.user.profile.email"
  expected_value: "user@example.com"
```

### Proposal-Based Governance
```yaml
# Create proposal
- type: propose
  node_name: node-1
  context_id: "{{ctx}}"
  author_id: "{{proposer_pub}}"
  actions:
    - type: "ExternalFunctionCall"
      method: "update_setting"
      args:
        key: "max_users"
        value: "1000"
  outputs:
    prop_id: "proposal_id"

# Approve proposal
- type: approve
  node_name: node-2
  context_id: "{{ctx}}"
  proposal_id: "{{prop_id}}"
  approver_id: "{{approver_pub}}"
```

## Tips for LLM Assistance

When asking an LLM to help with Merobox:

1. **Specify the goal**: "Create a workflow that tests X"
2. **Provide context**: Number of nodes, application behavior
3. **Share error messages**: Include full error output
4. **Mention constraints**: Specific versions, environment limitations
5. **Include relevant YAML**: Share existing workflow snippets

### Example Request
"I need a Merobox workflow that:
- Starts 2 nodes
- Installs a key-value store app
- Has node-1 write a value
- Has node-2 read and verify that value
- Should include proper wait times for sync"

## Resources

- **GitHub**: https://github.com/calimero-network/merobox
- **PyPI**: https://pypi.org/project/merobox/
- **Examples**: Check `workflow-examples/` directory in the repository
- **Issues**: Report bugs or request features on GitHub

## Quick Reference

```bash
# Essential commands
merobox run --name <node>          # Start node
merobox list                       # List nodes
merobox health <node>              # Check health
merobox logs <node>                # View logs
merobox install <node> <wasm>      # Install app
merobox bootstrap run <yaml>       # Run workflow
merobox nuke <node>                # Delete node data

# Workflow step types
install, context, identity, join, execute, wait, 
repeat, script, assert, json_assert, propose, approve
```

