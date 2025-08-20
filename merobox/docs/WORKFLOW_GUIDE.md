# Merobox Workflow Guide

This guide covers how to create, configure, and execute workflows using Merobox.

## Table of Contents

- [Basic Workflow Structure](#basic-workflow-structure)
- [Step Types](#step-types)
- [Dynamic Variables](#dynamic-variables)
- [Advanced Patterns](#advanced-patterns)
- [Examples](#examples)

## Basic Workflow Structure

Workflows are defined in YAML files and support complex orchestration patterns:

```yaml
metadata:
  name: "My Workflow"
  description: "Example workflow description"
  version: "1.0"

global_config:
  stop_all_nodes: false
  restart_nodes: false
  node_count: 2

steps:
  - name: "Start Nodes"
    type: "wait"
    config:
      duration: 5
```

### Required Fields

- **`metadata.name`**: Human-readable workflow name
- **`metadata.description`**: Workflow description
- **`global_config.node_count`**: Number of Calimero nodes to start
- **`steps`**: Array of workflow steps to execute

## Step Types

### `install` - Install Applications

Install applications on nodes.

```yaml
- name: "Install App"
  type: "install"
  config:
    node: "calimero-node-1"
    path: "./app.wasm"
    outputs:
      app_id: "applicationId"
```

**Required Fields:**
- `node`: Target node name
- Either `path` (local file) or `url` (remote URL)

**Optional Fields:**
- `dev`: Enable development mode
- `outputs`: Variable mappings to export

### `context` - Create Blockchain Contexts

Create blockchain contexts for applications.

```yaml
- name: "Create Context"
  type: "context"
  config:
    node: "calimero-node-1"
    application_id: "{{app_id}}"
    params:
      param1: "value1"
    outputs:
      context_id: "contextId"
      member_key: "memberPublicKey"
```

**Required Fields:**
- `node`: Target node name
- `application_id`: Application ID to create context for

**Optional Fields:**
- `params`: Context parameters
- `outputs`: Variable mappings to export

### `identity` - Create Cryptographic Identities

Create cryptographic identities on nodes.

```yaml
- name: "Create Identity"
  type: "identity"
  config:
    node: "calimero-node-2"
    outputs:
      public_key: "publicKey"
```

**Required Fields:**
- `node`: Target node name

**Optional Fields:**
- `outputs`: Variable mappings to export

### `invite` - Send Context Invitations

Send context invitations to other nodes.

```yaml
- name: "Invite Node"
  type: "invite"
  config:
    node: "calimero-node-1"
    context_id: "{{context_id}}"
    invitee_id: "{{public_key}}"
    outputs:
      invitation: "invitation"
```

**Required Fields:**
- `node`: Source node name
- `context_id`: Context to invite to
- `invitee_id`: Public key of invitee

**Optional Fields:**
- `outputs`: Variable mappings to export

### `join` - Join Contexts

Join contexts using invitations.

```yaml
- name: "Join Context"
  type: "join"
  config:
    node: "calimero-node-2"
    invitation: "{{invitation}}"
```

**Required Fields:**
- `node`: Target node name
- `invitation`: Invitation string from invite step

### `call` - Execute Function Calls

Execute smart contract function calls.

```yaml
- name: "Execute Function"
  type: "call"
  config:
    node: "calimero-node-1"
    context_id: "{{context_id}}"
    method: "set"
    args:
      key: "example_{{current_iteration}}"
      value: "data_{{current_iteration}}"
    executor_public_key: "{{member_key}}"
    exec_type: "sync"
```

**Required Fields:**
- `node`: Target node name
- `context_id`: Context to execute in
- `method`: Function name to call

**Optional Fields:**
- `args`: Function arguments
- `executor_public_key`: Public key for execution
- `exec_type`: Execution type (`sync` or `async`)

### `wait` - Add Delays

Add delays between steps.

```yaml
- name: "Wait"
  type: "wait"
  config:
    seconds: 10
```

**Optional Fields:**
- `seconds`: Duration to wait (default: 5)

### `repeat` - Execute Nested Steps

Execute nested steps multiple times.

```yaml
- name: "Repeat Operations"
  type: "repeat"
  config:
    count: 3
    outputs:
      current_iteration: "iteration"
    steps:
      - name: "Set Data"
        type: "call"
        config:
          method: "set"
          args:
            key: "key_{{current_iteration}}"
            value: "value_{{current_iteration}}"
```

**Required Fields:**
- `count`: Number of iterations
- `steps`: Array of steps to repeat

**Optional Fields:**
- `outputs`: Variable mappings to export

### `script` - Execute Custom Scripts

Execute custom scripts on nodes or Docker images.

```yaml
- name: "Run Script"
  type: "script"
  config:
    script: "echo 'Hello from {{current_iteration}}'"
    target: "nodes"
    description: "Custom script execution"
```

**Required Fields:**
- `script`: Script content to execute

**Optional Fields:**
- `target`: Target environment (`nodes` or `image`)
- `description`: Human-readable description

## Dynamic Variables

The workflow system supports powerful dynamic variable resolution:

**Important**: Variables are NOT automatically exported. They must be explicitly specified in the `outputs` configuration of each step.

### Built-in Variables

- `{{iteration}}`: Current iteration number (1-based)
- `{{iteration_index}}`: Current iteration index (0-based)
- `{{iteration_zero_based}}`: Same as iteration_index
- `{{iteration_one_based}}`: Same as iteration

### Custom Variables

Define custom variable mappings in step outputs:

```yaml
outputs:
  app_id: "applicationId"
  context_id: "contextId"
  public_key: "publicKey"
```

### Embedded Placeholders

Variables can be embedded within strings:

```yaml
args:
  key: "complex_key_{{current_iteration}}_suffix"
  value: "data_for_iteration_{{current_iteration}}"
```

### Explicit Export Configuration

All variables must be explicitly configured to be exported. For example, to export the context ID and member public key:

```yaml
- name: "Create Context"
  type: "context"
  config:
    node: "calimero-node-1"
    application_id: "{{app_id}}"
    outputs:
      context_id: "contextId"
      member_key: "memberPublicKey"
```

## Advanced Patterns

### Complex Data Flows

Chain multiple operations with variable passing:

```yaml
steps:
  - name: "Setup"
    type: "install"
    config:
      path: "./app.wasm"
      outputs:
        app_id: "applicationId"

  - name: "Create Environment"
    type: "context"
    config:
      application_id: "{{app_id}}"  # Use from previous step
      outputs:
        ctx_id: "contextId"

  - name: "Process Data"
    type: "repeat"
    config:
      count: 5
      outputs:
        current_iter: "iteration"
      steps:
        - type: "call"
          config:
            context_id: "{{ctx_id}}"  # Use from context step
            method: "process"
            args:
              data_id: "batch_{{current_iter}}"  # Use iteration variable
```

### Multi-Node Context Sharing

```yaml
metadata:
  name: "Multi-Node Setup"
  description: "Set up shared context across multiple nodes"

global_config:
  node_count: 2
  stop_all_nodes: true

steps:
  - name: "Install App on Node 1"
    type: "install"
    config:
      node: "calimero-node-1"
      path: "./app.wasm"
      outputs:
        app_id: "applicationId"

  - name: "Create Context"
    type: "context"
    config:
      node: "calimero-node-1"
      application_id: "{{app_id}}"
      outputs:
        context_id: "contextId"
        member_key: "memberPublicKey"

  - name: "Create Identity on Node 2"
    type: "identity"
    config:
      node: "calimero-node-2"
      outputs:
        invitee_key: "publicKey"

  - name: "Invite Node 2"
    type: "invite"
    config:
      node: "calimero-node-1"
      context_id: "{{context_id}}"
      invitee_id: "{{invitee_key}}"
      outputs:
        invitation: "invitation"

  - name: "Join from Node 2"
    type: "join"
    config:
      node: "calimero-node-2"
      invitation: "{{invitation}}"

  - name: "Test Cross-Node Operations"
    type: "call"
    config:
      node: "calimero-node-2"
      context_id: "{{context_id}}"
      method: "get"
      args:
        key: "shared_data"
      executor_public_key: "{{invitee_key}}"
```

## Examples

### Simple Node Setup

```yaml
metadata:
  name: "Basic Node Setup"
  description: "Start and configure basic Calimero nodes"

global_config:
  node_count: 2
  stop_all_nodes: false

steps:
  - name: "Wait for nodes to start"
    type: "wait"
    config:
      seconds: 10

  - name: "Check node health"
    type: "script"
    config:
      script: "echo 'Nodes are ready'"
      target: "nodes"
```

### Data Processing Workflow

```yaml
metadata:
  name: "Data Processing"
  description: "Process data in batches with Calimero"

global_config:
  node_count: 1
  stop_all_nodes: false

steps:
  - name: "Install Data App"
    type: "install"
    config:
      path: "./data_processor.wasm"
      outputs:
        app_id: "applicationId"

  - name: "Create Processing Context"
    type: "context"
    config:
      application_id: "{{app_id}}"
      outputs:
        context_id: "contextId"

  - name: "Process Batches"
    type: "repeat"
    config:
      count: 10
      outputs:
        batch_num: "iteration"
      steps:
        - name: "Process Batch"
          type: "call"
          config:
            context_id: "{{context_id}}"
            method: "process_batch"
            args:
              batch_id: "{{batch_num}}"
              data: "sample_data_{{batch_num}}"

        - name: "Wait between batches"
          type: "wait"
          config:
            seconds: 2
```

## Best Practices

1. **Always use outputs**: Explicitly define what variables each step exports
2. **Plan variable flow**: Design how data flows between steps before writing
3. **Use meaningful names**: Give steps and variables descriptive names
4. **Test incrementally**: Test individual steps before combining them
5. **Handle errors**: Use wait steps to ensure operations complete
6. **Document complex flows**: Add comments for complex workflows

## Troubleshooting

### Common Issues

- **Variable not found**: Ensure the variable is exported by a previous step
- **Step fails**: Check node health and logs
- **Timing issues**: Add appropriate wait steps between operations

### Debugging

- Use verbose mode: `merobox bootstrap -v workflow.yml`
- Check node logs: `merobox logs <node-name>`
- Verify node health: `merobox health`

For more information, see the [Command Reference](../README.md#commands) and [Examples](../README.md#examples) sections.
