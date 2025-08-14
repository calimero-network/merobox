# Calimero Bootstrap Command

The bootstrap command automates your Calimero node workflows using YAML configuration files, eliminating the need to manually execute each step.

## Overview

Instead of manually running:
1. ✅ Stop all nodes 
2. ✅ Start 2 nodes 
3. ✅ Install application on node 1
4. ✅ Create context on node 1 
5. ✅ Create identity on node 2 
6. ✅ Invite node 2 from node 1
7. ✅ Join context from node 2
8. ✅ Execute contract calls and view calls

You can now run a single command: `merobox bootstrap workflow-bootstrap.yml`

## Installation

First, install the required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### 1. Create a Sample Workflow

Generate a sample workflow configuration file:

```bash
merobox bootstrap create-sample
```

This creates `workflow-example.yml` with a template you can customize.

### 2. Run a Workflow

Execute a workflow from a YAML configuration file:

```bash
merobox bootstrap workflow-bootstrap.yml
```

### 3. Validate Configuration

Check if your workflow configuration is valid:

```bash
merobox bootstrap validate workflow-bootstrap.yml
```

## Dynamic Values and Placeholders

The bootstrap command automatically captures dynamic values from each step and makes them available to subsequent steps using placeholders. This eliminates the need to hardcode values that are generated during execution.

### Placeholder Format

Placeholders use the format `{{type.node_name}}` where:
- `type` is the step type (`install`, `context`, `identity`, `invite`, `join`)
- `node_name` is the name of the node that executed the step

### Example Workflow with Dynamic Values

```yaml
steps:
  - name: 'Install Application on Node 1'
    type: 'install_application'
    node: 'calimero-node-1'
    path: './kv_store.wasm'
    dev: true
    
  - name: 'Create Context on Node 1'
    type: 'create_context'
    node: 'calimero-node-1'
    application_id: '{{install.calimero-node-1}}'  # Uses result from install step
    
  - name: 'Create Identity on Node 2'
    type: 'create_identity'
    node: 'calimero-node-2'
    
  - name: 'Invite Node 2 from Node 1'
    type: 'invite_identity'
    node: 'calimero-node-1'
    context_id: '{{context.calimero-node-1}}'      # Uses result from context step
    granter_id: '{{context.calimero-node-1.memberPublicKey}}'  # Uses specific field
    grantee_id: '{{identity.calimero-node-2}}'     # Uses result from identity step
    capability: 'member'
    
  - name: 'Join Context from Node 2'
    type: 'join_context'
    node: 'calimero-node-2'
    context_id: '{{context.calimero-node-1}}'      # Uses result from context step
    invitee_id: '{{identity.calimero-node-2}}'     # Uses result from identity step
    invitation: '{{invite.calimero-node-1_identity.calimero-node-2}}'  # Uses invite result
    
  - name: 'Execute Contract Call'
    type: 'call'
    node: 'calimero-node-1'
    context_id: '{{context.calimero-node-1}}'
    method: 'set'
    args:
      key: hello
      value: world
```

### How Dynamic Values Work

1. **Install Step**: Captures the application ID returned from the installation
2. **Context Step**: Uses the captured application ID to create a context, then captures the context ID and member public key
3. **Identity Steps**: Capture the public keys returned from identity creation
4. **Invite Step**: Uses all the captured values (context ID, granter public key, grantee public key)
5. **Join Step**: Uses the context ID, invitee identity, and invitation data from previous steps
6. **Execute Step**: Automatically detects and uses the correct executor public key from the context

## Workflow Configuration

The YAML configuration file defines your workflow:

```yaml
name: 'Calimero Bootstrap Workflow'
description: 'Automated workflow for setting up Calimero nodes'
stop_all_nodes: true          # Stop existing nodes before starting
wait_timeout: 60              # Wait up to 60 seconds for nodes to be ready

nodes:
  count: 2                    # Start 2 nodes
  prefix: 'calimero-node'     # Node naming: calimero-node-1, calimero-node-2
  chain_id: 'testnet-1'       # Chain identifier
  image: 'ghcr.io/calimero-network/merod:6a47604'

steps:
  - name: 'Install Application on Node 1'
    type: 'install_application'
    node: 'calimero-node-1'
    path: './kv_store.wasm'   # Local WASM file path
    dev: true                 # Development installation
    
  - name: 'Create Context on Node 1'
    type: 'create_context'
    node: 'calimero-node-1'
    application_id: '{{install.calimero-node-1}}'  # Dynamic placeholder
    
  - name: 'Create Identity on Node 2'
    type: 'create_identity'
    node: 'calimero-node-2'
    
  - name: 'Wait for Identity Creation'
    type: 'wait'
    seconds: 5                # Wait 5 seconds
    
  - name: 'Invite Node 2 from Node 1'
    type: 'invite_identity'
    node: 'calimero-node-1'
    context_id: '{{context.calimero-node-1}}'      # Dynamic placeholder
    granter_id: '{{context.calimero-node-1.memberPublicKey}}'  # Dynamic placeholder
    grantee_id: '{{identity.calimero-node-2}}'     # Dynamic placeholder
    capability: 'member'
    
  - name: 'Join Context from Node 2'
    type: 'join_context'
    node: 'calimero-node-2'
    context_id: '{{context.calimero-node-1}}'      # Dynamic placeholder
    invitee_id: '{{identity.calimero-node-2}}'     # Dynamic placeholder
    invitation: '{{invite.calimero-node-1_identity.calimero-node-2}}'  # Dynamic placeholder
    
  - name: 'Execute Contract Call'
    type: 'call'
    node: 'calimero-node-1'
    context_id: '{{context.calimero-node-1}}'
    method: 'set'
    args:
      key: hello
      value: world
```

## Step Types

### `install_application`
Installs an application on a specified node.

**Options:**
- `node`: Target node name
- `path`: Local file path (for dev installation)
- `url`: Remote URL (for production installation)
- `dev`: Boolean flag for development installation

**Dynamic Values Captured:**
- Application ID (stored as `{{install.node_name}}`)

### `create_context`
Creates a context for an application.

**Options:**
- `node`: Target node name
- `application_id`: Application identifier (can use `{{install.node_name}}` placeholder)

**Dynamic Values Captured:**
- Context ID (stored as `{{context.node_name}}`)
- Member Public Key (accessible as `{{context.node_name.memberPublicKey}}`)

### `create_identity`
Generates a new identity on a node.

**Options:**
- `node`: Target node name

**Dynamic Values Captured:**
- Public key (stored as `{{identity.node_name}}`)

### `invite_identity`
Invites an identity to a context.

**Options:**
- `node`: Node performing the invitation
- `context_id`: Context identifier (use `{{context.node_name}}` placeholder)
- `granter_id`: Public key of the granter (use `{{context.node_name.memberPublicKey}}` placeholder)
- `grantee_id`: Public key of the grantee (use `{{identity.node_name}}` placeholder)
- `capability`: Permission level (default: 'member')

**Dynamic Values Captured:**
- Invitation data (stored as `{{invite.node_name_identity.node_name}}`)

### `join_context`
Joins a context using an invitation.

**Options:**
- `node`: Node joining the context
- `context_id`: Context identifier (use `{{context.node_name}}` placeholder)
- `invitee_id`: Public key of the identity joining (use `{{identity.node_name}}` placeholder)
- `invitation`: Invitation data from the invite step (use `{{invite.node_name_identity.node_name}}` placeholder)

**Dynamic Values Captured:**
- Join result data (stored as `{{join.node_name_identity.node_name}}`)

### `call`
Executes contract calls, view calls, or function calls.

**Options:**
- `node`: Target node name
- `context_id`: Context identifier (use `{{context.node_name}}` placeholder)
- `method`: Method/function name to call
- `args`: Arguments for the method call
- `exec_type`: Execution type (optional, defaults to 'function_call')

**Features:**
- Automatically detects and uses the correct executor public key from the context
- Supports complex argument structures
- Comprehensive error reporting and debugging information

### `wait`
Pauses execution for a specified duration.

**Options:**
- `seconds`: Number of seconds to wait

## Node Configuration

### Multiple Nodes (Simple)
```yaml
nodes:
  count: 2
  prefix: 'calimero-node'
  chain_id: 'testnet-1'
```

### Individual Node Configuration
```yaml
nodes:
  node1:
    port: 2428
    rpc_port: 2528
    chain_id: 'testnet-1'
    data_dir: './data/custom-node1'
  node2:
    port: 2429
    rpc_port: 2529
    chain_id: 'testnet-1'
```

## Example Workflow

The included `workflow-example.yml` file demonstrates a complete workflow that:
- Stops all existing nodes
- Starts 2 new nodes
- Installs the KV store application on node 1
- Creates a context for the application (using captured application ID)
- Generates an identity on node 2
- Invites node 2 to the context (using captured context ID and public keys)
- Joins the context from node 2 (using captured invitation data)
- Executes contract calls and view calls (with automatic executor detection)

## Advanced Features

### Field-Specific Placeholders
Access specific fields from captured data:
```yaml
granter_id: '{{context.calimero-node-1.memberPublicKey}}'  # Access specific field
```

### Complex Dynamic Value Resolution
The system automatically resolves nested placeholders and handles complex data structures:
```yaml
invitation: '{{invite.calimero-node-1_identity.calimero-node-2}}'  # Complex resolution
```

### Automatic Executor Detection
Execute steps automatically detect and use the correct executor public key from the context:
```yaml
- name: 'Execute Contract Call'
  type: 'call'
  node: 'calimero-node-1'
  context_id: '{{context.calimero-node-1}}'  # Executor key automatically extracted
  method: 'set'
  args:
    key: hello
    value: world
```

## Troubleshooting

- **Validation**: Use `merobox bootstrap validate <file>` to check your configuration
- **Verbose Output**: Add `--verbose` flag for detailed execution information
- **Node Readiness**: The system automatically waits for nodes to be ready before proceeding
- **Error Handling**: Each step is validated and the workflow stops on first failure
- **Dynamic Values**: Check the output for captured dynamic values to ensure placeholders are working
- **Debug Information**: The system provides detailed debugging information for complex operations

## Extending the Workflow

You can add custom steps by extending the `WorkflowExecutor` class in `commands/bootstrap.py`. The modular design makes it easy to add new step types and functionality.

## Benefits of Dynamic Values

1. **No Hardcoding**: Values are automatically captured and used
2. **Reliability**: Eliminates manual errors from copying/pasting values
3. **Flexibility**: Workflows work with any application or identity
4. **Maintainability**: Changes to one step automatically propagate to dependent steps
5. **Debugging**: Clear visibility into what values were captured and used
6. **Complex Operations**: Support for multi-step workflows with automatic data flow
7. **Field Access**: Access to specific fields within captured data structures
