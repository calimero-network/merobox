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

### Blob Storage Management

```bash
# Upload a file to blob storage
merobox blob upload --node my-node --file /path/to/file.txt

# Upload with context association
merobox blob upload --node my-node --file /path/to/file.txt --context-id <context-id>

# List all blobs on a node
merobox blob list-blobs --node my-node

# Download a blob
merobox blob download --node my-node --blob-id <blob-id> --output /path/to/output.txt

# Get blob metadata
merobox blob info --node my-node --blob-id <blob-id>

# Delete a blob
merobox blob delete --node my-node --blob-id <blob-id>

# Delete without confirmation
merobox blob delete --node my-node --blob-id <blob-id> --yes
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
  - type: install_application
    node: node-1
    path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: call
    node: node-1
    application_id: "{{app_id}}"
    method: "initialize"
    args:
      key: "value"
```

### Workflow Configuration Options

Workflows support several top-level configuration options:

#### Authentication Service

Enable authentication service integration with Traefik proxy. When enabled, nodes are automatically configured with authentication middleware and proper routing.

```yaml
name: Workflow with Auth Service
description: "Workflow with authentication enabled"

# Enable auth service for this workflow
auth_service: true

# Optional: Specify custom auth image
auth_image: "ghcr.io/calimero-network/mero-auth:edge"

# Optional: Use cached auth frontend (default: false)
auth_use_cached: true

nodes:
  count: 1
  prefix: "calimero-node"
  image: "ghcr.io/calimero-network/merod:edge"

steps:
  - type: wait
    seconds: 5
    message: "Waiting for node to start with auth service..."
```

**What gets enabled:**
- **Traefik Proxy**: Routes traffic and applies authentication middleware
- **Auth Service**: Handles authentication and authorization
- **Protected Routes**: `/admin-api/`, `/jsonrpc`, `/ws` require authentication
- **Public Routes**: `/admin-dashboard` remains publicly accessible
- **Auth Routes**: `/auth/login` for authentication

**URL Access with Auth Service:**
- Node URLs: `http://node1.127.0.0.1.nip.io`
- Auth Login: `http://node1.127.0.0.1.nip.io/auth/login`
- Admin Dashboard: `http://node1.127.0.0.1.nip.io/admin-dashboard`

**Note:** Auth service can also be enabled via CLI flag:
```bash
merobox bootstrap run workflow.yml --auth-service
```

**Note:** Auth service is not supported in binary mode (`no_docker: true`).

### Available Step Types

#### 1. Install Application Step
Install a WASM application on a node.

```yaml
- type: install_application
  node: node-1
  path: ./my-app.wasm
  outputs:
    app_id: "application_id"
```

#### 2. Create Context Step
Create a new context on a node.

```yaml
- type: create_context
  node: node-1
  application_id: "{{app_id}}"
  outputs:
    context_id: "context.context_id"
    context_seed: "context.seed"
```

#### 3. Create Identity Step
Create a new identity for a node.

```yaml
- type: create_identity
  node: node-1
  outputs:
    private_key: "private_key"
    public_key: "public_key"
```

#### 4. Join Context Step
Join a node to a context using an invitation.

```yaml
- name: Join Context from Node 2
  type: join_context
  node: node-2
  context_id: "{{context_id}}"
  invitee_id: "{{public_key}}"
  invitation: "{{invitation}}"
```

#### 5. Call Step
Call a method on an application.

```yaml
- type: call
  node: node-1
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
  count: 3
  variable: iteration
  steps:
    - type: call
      node: node-1
      context_id: "{{context_id}}"
      method: "process_{{iteration}}"
      executor_public_key: "{{public_key}}"
  outputs:
    - key: "result_{{iteration}}"
      value: "output"
```

#### 14. Fuzzy Test Step
Run long-duration randomized load tests (30-60+ minutes) with weighted operation patterns and assertion-based validation.

```yaml
- type: fuzzy_test
  duration_minutes: 30
  context_id: "{{context_id}}"
  success_threshold: 95.0  # Pass if 95%+ assertions succeed
  
  nodes:
    - name: calimero-node-1
      executor_key: "{{member_public_key}}"
    - name: calimero-node-2
      executor_key: "{{public_key_node2}}"
  
  operations:
    # Pattern with 40% frequency
    - name: "set_and_verify"
      weight: 40
      steps:
        - type: call
          node: "{{random_node}}"  # Random node
          method: set
          context_id: "{{context_id}}"
          executor_public_key: "{{random_executor}}"
          args:
            key: "test_{{random_int(1, 1000)}}"  # Random values
            value: "{{uuid}}_{{timestamp}}"
          outputs:
            test_key: args.key
            test_value: args.value
        
        - type: wait
          seconds: 1
        
        - type: call
          node: "{{random_node}}"
          method: get
          context_id: "{{context_id}}"
          executor_public_key: "{{random_executor}}"
          args:
            key: "{{test_key}}"
          outputs:
            retrieved: result
        
        - type: assert
          non_blocking: true  # Don't stop on failure
          statements:
            - statement: "contains({{retrieved}}, {{test_value}})"
              message: "Value should match"
    
    # Cross-node test with 30% frequency
    - name: "cross_node_sync"
      weight: 30
      steps:
        - type: call
          node: calimero-node-1
          method: set
          args:
            key: "sync_{{random_int(1, 500)}}"
            value: "{{timestamp}}"
          # ... rest of pattern
```

**Random Generators Available:**
- `{{random_int(min, max)}}` - Random integer
- `{{random_string(length)}}` - Random string
- `{{random_float(min, max)}}` - Random float
- `{{random_choice([a, b, c])}}` - Random choice
- `{{timestamp}}` - Unix timestamp
- `{{uuid}}` - Random UUID
- `{{random_node}}` - Random node from list
- `{{random_executor}}` - Random executor key

**Why Use Fuzzy Testing:**
- Discover memory leaks over time
- Find race conditions under load
- Test data propagation reliability
- Validate performance degradation
- Stress test with realistic patterns

#### 8. Script Step
Execute shell scripts or commands.

```yaml
- type: script
  description: "Execute setup script"
  script: "./scripts/setup.sh"
  target: "image"  # or "nodes"
```

#### 9. Assertion Steps
Validate execution results.

```yaml
# Simple assertion with statements
- type: assert
  statements:
    - "is_set({{context_id}})"
    - "contains({{result}}, 'expected_value')"
    - "{{value}} == 'expected'"

# JSON assertion with equality and subset checks
- type: json_assert
  statements:
    - 'json_equal({{result}}, {"key": "value"})'
    - 'json_subset({{result}}, {"key": "value"})'
```

#### 10. Proposals
Create and vote on proposals in a context.

```yaml
# Create a proposal
- type: call
  node: node-1
  context_id: "{{context_id}}"
  executor_public_key: "{{public_key}}"
  method: create_new_proposal
  args:
    request:
      action_type: "SetContextValue"
      params:
        key: "config_key"
        value: "config_value"
  outputs:
    proposal_id:
      field: result
      path: output

# Approve a proposal
- type: call
  node: node-2
  context_id: "{{context_id}}"
  executor_public_key: "{{approver_public_key}}"
  method: approve_proposal
  args:
    proposal_id: "{{proposal_id}}"
  outputs:
    approval_result: result
```

#### 11. Blob Upload Step
Upload files to blob storage in workflows.

```yaml
- type: upload_blob
  node: node-1
  file_path: ./data/file.txt
  context_id: "{{context_id}}"  # Optional
  outputs:
    blob_id: "blob_id"
    blob_size: "size"
```

#### 12. Invite Open Step
Create open invitations for contexts (allows anyone to join without prior approval).

```yaml
- type: invite_open
  node: node-1
  context_id: "{{context_id}}"
  granter_id: "{{admin_public_key}}"
  valid_for_blocks: 1000  # Optional, defaults to 1000
  outputs:
    invitation: "invitation"
```

#### 13. Join Open Step
Join a context using an open invitation.

```yaml
- type: join_open
  node: node-2
  invitee_id: "{{new_member_public_key}}"
  invitation: "{{invitation}}"
  outputs:
    joined_context_id: "contextId"
    member_public_key: "memberPublicKey"
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
  - type: create_identity
    node: main-node
    outputs:
      admin_key: "private_key"
      admin_pub: "public_key"

  - type: install_application
    node: main-node
    path: ./app.wasm
    outputs:
      app: "application_id"

  - type: create_context
    node: main-node
    application_id: "{{app}}"
    outputs:
      ctx: "context.context_id"

  - type: call
    node: main-node
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
  - type: install_application
    node: node-1
    path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: create_context
    node: node-1
    application_id: "{{app_id}}"
    outputs:
      context_id: "context.context_id"
      seed: "context.seed"

  # Create identities for joining nodes
  - type: create_identity
    node: node-2
    outputs:
      node2_key: "private_key"
      node2_pub: "public_key"

  - type: create_identity
    node: node-3
    outputs:
      node3_key: "private_key"
      node3_pub: "public_key"

  # Invite nodes to join (you'll need to add invite_identity steps)
  # Then join additional nodes
  - name: Join Context from Node 2
    type: join_context
    node: node-2
    context_id: "{{context_id}}"
    invitee_id: "{{node2_pub}}"
    invitation: "{{invitation2}}"

  - name: Join Context from Node 3
    type: join_context
    node: node-3
    context_id: "{{context_id}}"
    invitee_id: "{{node3_pub}}"
    invitation: "{{invitation3}}"

  - type: wait
    seconds: 2

  # Verify all nodes can execute
  - type: call
    node: node-2
    context_id: "{{context_id}}"
    method: "ping"
    executor_public_key: "{{node2_pub}}"
```

## Common Patterns

### Pattern 1: Simple Testing Setup

```yaml
name: Quick Test
steps:
  - type: install_application
    node: test-node
    path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: create_context
    node: test-node
    application_id: "{{app_id}}"
    outputs:
      ctx_id: "context.context_id"

  - type: create_identity
    node: test-node
    outputs:
      pk: "private_key"
      pub: "public_key"

  - type: call
    node: test-node
    context_id: "{{ctx_id}}"
    method: "test_function"
    executor_public_key: "{{pub}}"
    outputs:
      test_result: "output"

  - type: assert
    statements:
      - "is_set({{test_result}})"
      - "contains({{test_result}}, 'success')"
```

### Pattern 2: Batch Operations with Repeat

```yaml
name: Batch Insert
steps:
  # ... setup steps ...

  - type: repeat
    count: 10
    variable: i
    steps:
      - type: call
        node: node-1
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
  - type: install_application
    node: sender
    path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: create_context
    node: sender
    application_id: "{{app_id}}"
    outputs:
      ctx: "context.context_id"

  - type: create_identity
    node: sender
    outputs:
      sender_key: "private_key"
      sender_pub: "public_key"

  - type: create_identity
    node: receiver
    outputs:
      receiver_key: "private_key"
      receiver_pub: "public_key"

  - name: Join Context from Receiver
    type: join_context
    node: receiver
    context_id: "{{ctx}}"
    invitee_id: "{{receiver_pub}}"
    invitation: "{{receiver_invitation}}"

  # Sender writes data
  - type: call
    node: sender
    context_id: "{{ctx}}"
    method: "write_message"
    args:
      msg: "Hello from sender"
    executor_public_key: "{{sender_pub}}"

  - type: wait
    seconds: 1

  # Receiver reads data
  - type: call
    node: receiver
    context_id: "{{ctx}}"
    method: "read_message"
    executor_public_key: "{{receiver_pub}}"
    outputs:
      message: "output"

  - type: assert
    statements:
      - "contains({{message}}, 'Hello from sender')"
```

### Pattern 4: Open Invitations (Public Context Joining)

```yaml
name: Public Context with Open Invitations
steps:
  # Setup context
  - type: install_application
    node: main-node
    path: ./app.wasm
    outputs:
      app_id: "application_id"

  - type: create_context
    node: main-node
    application_id: "{{app_id}}"
    outputs:
      ctx: "context.context_id"

  - type: create_identity
    node: main-node
    outputs:
      admin_key: "private_key"
      admin_pub: "public_key"

  # Create open invitation
  - type: invite_open
    node: main-node
    context_id: "{{ctx}}"
    granter_id: "{{admin_pub}}"
    valid_for_blocks: 2000
    outputs:
      open_invite: "invitation"

  # New member creates identity
  - type: create_identity
    node: secondary-node
    outputs:
      new_member_pub: "public_key"

  # New member joins via open invitation
  - type: join_open
    node: secondary-node
    invitee_id: "{{new_member_pub}}"
    invitation: "{{open_invite}}"

  - type: wait
    seconds: 2

  # Verify new member can participate
  - type: call
    node: secondary-node
    context_id: "{{ctx}}"
    method: "ping"
    executor_public_key: "{{new_member_pub}}"
```

### Pattern 5: Blob Storage in Workflows

```yaml
name: Data Upload and Processing
steps:
  - type: install_application
    node: data-node
    path: ./processor.wasm
    outputs:
      app_id: "application_id"

  - type: create_context
    node: data-node
    application_id: "{{app_id}}"
    outputs:
      ctx: "context.context_id"

  # Upload data file to blob storage
  - type: upload_blob
    node: data-node
    file_path: ./data/input.json
    context_id: "{{ctx}}"
    outputs:
      data_blob_id: "blob_id"

  - type: create_identity
    node: data-node
    outputs:
      processor_pub: "public_key"

  # Process the uploaded blob
  - type: call
    node: data-node
    context_id: "{{ctx}}"
    method: "process_blob"
    args:
      blob_id: "{{data_blob_id}}"
    executor_public_key: "{{processor_pub}}"
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
- name: Join Context from Node 2
  type: join_context
  node: node-2
  context_id: "{{ctx}}"
  invitee_id: "{{public_key}}"
  invitation: "{{invitation}}"

- type: wait
  seconds: 2  # Allow node to fully sync
```

### 3. Use Assertions to Validate State
```yaml
- type: call
  node: node-1
  context_id: "{{ctx}}"
  method: "set_value"
  args:
    key: "test"
    value: "123"
  executor_public_key: "{{pub_key}}"
  outputs:
    result: "output"

- type: assert
  statements:
    - "is_set({{result}})"
    - "contains({{result}}, '123')"
```

### 4. Organize Complex Workflows with Scripts
```yaml
- type: script
  description: "Setup environment"
  script: "./setup-environment.sh"
  target: "image"
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
- type: call
  node: "{{env.NODE_NAME}}"
  context_id: "{{env.CONTEXT_ID}}"
  method: "process"
  executor_public_key: "{{public_key}}"
```

### JSON Assertions
```yaml
- type: json_assert
  statements:
    - 'json_equal({{user_data}}, {"email": "user@example.com"})'
    - 'json_subset({{user_data}}, {"profile": {"email": "user@example.com"}})'
```

### Proposal-Based Governance
```yaml
# Create proposal
- type: call
  node: node-1
  context_id: "{{ctx}}"
  executor_public_key: "{{proposer_pub}}"
  method: create_new_proposal
  args:
    request:
      action_type: "SetContextValue"
      params:
        key: "max_users"
        value: "1000"
  outputs:
    prop_id:
      field: result
      path: output

# Approve proposal
- type: call
  node: node-2
  context_id: "{{ctx}}"
  executor_public_key: "{{approver_pub}}"
  method: approve_proposal
  args:
    proposal_id: "{{prop_id}}"
  outputs:
    approval_result: result
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

## Fuzzy Load Testing

Fuzzy load testing runs randomized operations for extended periods (30-60+ minutes) to discover issues that only appear under sustained load.

### Quick Example

```yaml
- type: fuzzy_test
  duration_minutes: 30
  context_id: "{{context_id}}"
  nodes:
    - name: node-1
      executor_key: "{{key1}}"
    - name: node-2
      executor_key: "{{key2}}"
  operations:
    - name: "write_and_read"
      weight: 70  # 70% of operations
      steps:
        - type: call
          node: "{{random_node}}"
          method: set
          args:
            key: "k_{{random_int(1, 1000)}}"
            value: "v_{{uuid}}"
        - type: assert
          non_blocking: true
          statements:
            - "is_set({{result}})"
```

### What Gets Tested
- Memory leaks over time
- Race conditions under load
- Data propagation reliability
- Performance degradation
- Cross-node consistency

### Key Features
- **Application-agnostic**: Works with any contract
- **Weighted patterns**: Control operation frequency (e.g., 70% reads, 30% writes)
- **Non-blocking assertions**: Failures tracked but don't stop test
- **Random generators**: `{{random_int()}}`, `{{uuid}}`, `{{timestamp}}`, etc.
- **Live reporting**: Progress summaries every 60 seconds
- **Final analysis**: Detailed report with pass rates and failure patterns

See `workflow-examples/FUZZY-LOAD-TESTING.md` for complete guide.

## Resources

- **GitHub**: https://github.com/calimero-network/merobox
- **PyPI**: https://pypi.org/project/merobox/
- **Examples**: Check `workflow-examples/` directory in the repository
- **Fuzzy Testing**: `workflow-examples/workflow-fuzzy-kv-store.yml` and `FUZZY-LOAD-TESTING.md`
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

# Blob storage commands
merobox blob upload --node <node> --file <path>    # Upload file
merobox blob list-blobs --node <node>              # List blobs
merobox blob download --node <node> --blob-id <id> --output <path>
merobox blob info --node <node> --blob-id <id>     # Get metadata
merobox blob delete --node <node> --blob-id <id>   # Delete blob

# Workflow step types
install_application, create_context, create_identity, join_context, call, wait, 
repeat, script, assert, json_assert, upload_blob, invite_open, join_open, fuzzy_test
```

