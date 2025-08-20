# Dynamic Variables Documentation

This document describes all the dynamic variables that can be exported by each step type in the Merobox workflow system. Each step explicitly defines which variables it can export and maps them to specific keys in the `dynamic_values` dictionary.

## Overview

Dynamic variables are automatically captured from step execution results and made available for use in subsequent steps using placeholder syntax like `{{variable_name}}`. The system enforces explicit declaration of exportable variables to ensure transparency and prevent unexpected behavior.

## Two Export Methods

### 1. **Automatic Exports** (Default)
Steps automatically export predefined variables based on their configuration.

### 2. **Custom Outputs** (User-Controlled)
Users can specify exactly which variables to export and what names to use:

```yaml
outputs:
  my_app_id: id                    # Simple: export 'id' field as 'my_app_id'
  app_name: name                   # Simple: export 'name' field as 'app_name'
  custom_path: path                # Simple: export 'path' field as 'custom_path'
  node_specific_id:                # Complex: with node name replacement
    field: id
    target: app_id_{node_name}
```

## Step Types and Exportable Variables

### 1. Install Application Step (`install_application`)

**Purpose**: Installs applications on Calimero nodes

**Available Fields**:
- `id` - Application ID (primary identifier)
- `applicationId` - Alternative application ID field
- `name` - Application name
- `path` - Local file path (for dev installations)
- `container_path` - Container file path (for dev installations)
- `url` - Remote URL (for remote installations)

**Example Usage with Custom Outputs**:
```yaml
- name: Install Application
  type: install_application
  node: calimero-node-1
  path: ./app.wasm
  dev: true
  outputs:
    my_app_id: id                    # Export 'id' as 'my_app_id'
    app_name: name                   # Export 'name' as 'app_name'
    custom_path: path                # Export 'path' as 'custom_path'
    node_specific_id:                # Export 'id' with node name
      field: id
      target: app_id_{node_name}
```

**Captured Variables**:
- `{{my_app_id}}` - Application ID (custom name)
- `{{app_name}}` - Application name (custom name)
- `{{custom_path}}` - Local file path (custom name)
- `{{app_id_calimero-node-1}}` - Node-specific application ID

### 2. Create Context Step (`create_context`)

**Purpose**: Creates contexts for installed applications

**Available Fields**:
- `id` - Context ID (primary identifier)
- `contextId` - Alternative context ID field
- `name` - Context name
- `memberPublicKey` - Public key of the context member
- `status` - Context status
- `applicationId` - ID of the application this context belongs to

**Example Usage with Custom Outputs**:
```yaml
- name: Create Context
  type: create_context
  node: calimero-node-1
  application_id: '{{my_app_id}}'
  outputs:
    my_context_id: id               # Export 'id' as 'my_context_id'
    member_key: memberPublicKey     # Export 'memberPublicKey' as 'member_key'
    context_status: status          # Export 'status' as 'context_status'
    node_context_id:                # Export 'id' with node name
      field: id
      target: context_id_{node_name}
```

**Captured Variables**:
- `{{my_context_id}}` - Context ID (custom name)
- `{{member_key}}` - Member public key (custom name)
- `{{context_status}}` - Context status (custom name)
- `{{context_id_calimero-node-1}}` - Node-specific context ID

### 3. Create Identity Step (`create_identity`)

**Purpose**: Generates new identities on nodes

**Available Fields**:
- `publicKey` - Public key of the generated identity
- `id` - Identity ID (alternative field)
- `name` - Identity name
- `status` - Identity status

**Example Usage with Custom Outputs**:
```yaml
- name: Create Identity
  type: create_identity
  node: calimero-node-2
  outputs:
    my_public_key: publicKey        # Export 'publicKey' as 'my_public_key'
    identity_name: name             # Export 'name' as 'identity_name'
    node_public_key:                # Export 'publicKey' with node name
      field: publicKey
      target: public_key_{node_name}
```

**Captured Variables**:
- `{{my_public_key}}` - Public key (custom name)
- `{{identity_name}}` - Identity name (custom name)
- `{{public_key_calimero-node-2}}` - Node-specific public key

### 4. Invite Identity Step (`invite_identity`)

**Purpose**: Invites identities to join contexts

**Available Fields**:
- `invitation` - Invitation data for joining the context
- `id` - Invitation ID
- `status` - Invitation status
- `contextId` - ID of the context being invited to

**Example Usage with Custom Outputs**:
```yaml
- name: Invite Identity
  type: invite_identity
  node: calimero-node-1
  context_id: '{{my_context_id}}'
  granter_id: '{{member_key}}'
  grantee_id: '{{my_public_key}}'
  capability: member
  outputs:
    my_invitation: invitation        # Export 'invitation' as 'my_invitation'
    invite_status: status            # Export 'status' as 'invite_status'
```

**Captured Variables**:
- `{{my_invitation}}` - Invitation data (custom name)
- `{{invite_status}}` - Invitation status (custom name)

### 5. Join Context Step (`join_context`)

**Purpose**: Joins contexts using invitations

**Available Fields**:
- `id` - Join operation ID
- `status` - Join operation status
- `contextId` - ID of the context joined
- `memberId` - ID of the member who joined
- `timestamp` - Timestamp of the join operation

**Example Usage with Custom Outputs**:
```yaml
- name: Join Context
  type: join_context
  node: calimero-node-2
  context_id: '{{my_context_id}}'
  invitee_id: '{{my_public_key}}'
  invitation: '{{my_invitation}}'
  outputs:
    join_status: status              # Export 'status' as 'join_status'
    join_timestamp: timestamp        # Export 'timestamp' as 'join_timestamp'
```

**Captured Variables**:
- `{{join_status}}` - Join operation status (custom name)
- `{{join_timestamp}}` - Join timestamp (custom name)

### 6. Execute Step (`call`)

**Purpose**: Executes contract calls, view calls, or function calls

**Available Fields**:
- `result` - Result of the function call
- `gasUsed` - Gas used for the execution
- `status` - Execution status
- `error` - Error message if execution failed
- `returnValue` - Return value from the function

**Example Usage with Custom Outputs**:
```yaml
- name: Execute Contract Call
  type: call
  node: calimero-node-1
  context_id: '{{my_context_id}}'
  method: set
  args:
    key: hello
    value: world
  outputs:
    call_result: result              # Export 'result' as 'call_result'
    gas_used: gasUsed               # Export 'gasUsed' as 'gas_used'
    execution_status: status        # Export 'status' as 'execution_status'
```

**Captured Variables**:
- `{{call_result}}` - Function call result (custom name)
- `{{gas_used}}` - Gas used (custom name)
- `{{execution_status}}` - Execution status (custom name)

### 7. Script Step (`script`)

**Purpose**: Executes custom scripts on Docker images or running nodes

**Available Fields**:
- `exit_code` - Script exit code
- `output` - Script output/result
- `execution_time` - Time taken to execute the script
- `script_path` - Path to the executed script
- `env_vars` - Environment variables set by the script

**Example Usage with Custom Outputs**:
```yaml
- name: Execute Pre-script
  type: script
  target: image
  script: ./scripts/setup.sh
  outputs:
    script_result: output            # Export 'output' as 'script_result'
    exit_code: exit_code            # Export 'exit_code' as 'exit_code'
    setup_time: execution_time      # Export 'execution_time' as 'setup_time'
```

**Captured Variables**:
- `{{script_result}}` - Script output (custom name)
- `{{exit_code}}` - Script exit code (custom name)
- `{{setup_time}}` - Execution time (custom name)

**Common Environment Variables Captured**:
- `NODE_READY` - Node readiness status
- `NODE_HOSTNAME` - Node hostname
- `NODE_TIMESTAMP` - Node timestamp
- `CALIMERO_HOME` - Calimero home directory
- `TOOLS_INSTALLED` - Tools installation status
- `CURL_AVAILABLE` - curl availability
- `PERF_AVAILABLE` - perf availability
- `PACKAGE_MANAGER` - Package manager type
- `UPDATE_CMD` - Update command
- `INSTALL_CMD` - Install command

### 8. Repeat Step (`repeat`)

**Purpose**: Executes nested steps multiple times

**Available Fields**:
- `iteration` - Current iteration number (1-based)
- `iteration_index` - Current iteration index (0-based)
- `iteration_zero_based` - Current iteration index (0-based, alias)
- `iteration_one_based` - Current iteration number (1-based, alias)
- `total_iterations` - Total number of iterations
- `current_step` - Current step being executed
- `step_count` - Total number of nested steps

**Example Usage with Custom Outputs**:
```yaml
- name: Repeat Operations
  type: repeat
  count: 3
  outputs:
    current_iteration: iteration     # Export 'iteration' as 'current_iteration'
    total_count: total_iterations   # Export 'total_iterations' as 'total_count'
  steps:
    - name: Set Key-Value
      type: call
      node: calimero-node-1
      context_id: '{{my_context_id}}'
      method: set
      args:
        key: "iteration_{{current_iteration}}"
        value: "value_{{current_iteration}}"
```

**Captured Variables**:
- `{{current_iteration}}` - Current iteration (1, 2, 3)
- `{{total_count}}` - Total iterations (3)

### 9. Wait Step (`wait`)

**Purpose**: Pauses execution for a specified duration

**Exportable Variables**: None

**Example Usage**:
```yaml
- name: Wait for Propagation
  type: wait
  seconds: 5
```

## Custom Outputs Configuration

### Simple Field Assignment
```yaml
outputs:
  my_variable: field_name           # Export 'field_name' as 'my_variable'
```

### Complex Field Assignment with Node Name Replacement
```yaml
outputs:
  node_specific_var:                # Export with node name replacement
    field: field_name               # Source field from API response
    target: var_{node_name}         # Target key with {node_name} placeholder
```

### Multiple Outputs
```yaml
outputs:
  app_id: id                        # Simple assignment
  app_name: name                    # Simple assignment
  node_app_id:                      # Complex assignment
    field: id
    target: app_id_{node_name}
  custom_path: path                 # Simple assignment
```

## Variable Naming Conventions

### Node-Specific Variables
Variables that are specific to a particular node use the pattern:
```
{variable_type}_{node_name}
```

Examples:
- `app_id_calimero-node-1`
- `context_id_calimero-node-2`
- `public_key_calimero-node-3`

### Method-Specific Variables
Variables that are specific to a particular method use the pattern:
```
{variable_type}_{node_name}_{method}
```

Examples:
- `execute_result_calimero-node-1_set`
- `execute_gas_used_calimero-node-1_get`

### Identity-Specific Variables
Variables that are specific to a particular identity use the pattern:
```
{variable_type}_{node_name}_{identity_id}
```

Examples:
- `invitation_data_calimero-node-1_abc123`
- `join_status_calimero-node-2_abc123`

## Using Dynamic Variables

### In Workflow Configuration
Dynamic variables can be used in workflow configuration files using placeholder syntax:

```yaml
- name: Use Captured Value
  type: call
  node: calimero-node-1
  context_id: '{{my_context_id}}'           # Custom output name
  method: set
  args:
    key: "app_id"
    value: '{{my_app_id}}'                  # Custom output name
```

### In Scripts
Dynamic variables are also available as environment variables in scripts:

```bash
#!/bin/bash
echo "Application ID: $MY_APP_ID"           # Custom output name
echo "Context ID: $MY_CONTEXT_ID"           # Custom output name
```

## Validation and Error Handling

The system automatically validates export configurations:
- Each step must define its exportable variables OR use custom outputs
- Warnings are shown for steps without export configurations
- Export failures are logged with detailed information
- Legacy support ensures backward compatibility
- Custom outputs take precedence over automatic exports

## Best Practices

1. **Use descriptive names**: Choose variable names that clearly indicate their purpose
2. **Be specific**: Only export the variables you actually need
3. **Use custom outputs**: Take control of variable naming for better workflow readability
4. **Handle missing values**: Use fallback values or error handling for critical variables
5. **Document dependencies**: Clearly document which variables are required by each step
6. **Test workflows**: Verify that all expected variables are captured and available

## Migration from Automatic to Custom Exports

### Before (Automatic)
```yaml
- name: Install Application
  type: install_application
  node: calimero-node-1
  path: ./app.wasm
  dev: true
# Automatically exports: app_id_calimero-node-1, app_name_calimero-node-1, etc.
```

### After (Custom)
```yaml
- name: Install Application
  type: install_application
  node: calimero-node-1
  path: ./app.wasm
  dev: true
  outputs:
    my_app_id: id                    # Custom name instead of app_id_calimero-node-1
    app_name: name                   # Custom name instead of app_name_calimero-node-1
```

## Troubleshooting

### Common Issues

1. **Variable not found**: Check if the step has executed successfully and exported the variable
2. **Wrong variable name**: Verify the exact variable name from the exportable variables list
3. **Node name mismatch**: Ensure the node name in the variable matches the actual node name
4. **Step execution order**: Variables are only available after the step that exports them has executed
5. **Custom output field missing**: Check if the field name exists in the API response

### Debug Information

The system provides detailed logging for variable exports:
- Blue text shows successful exports
- Yellow text shows warnings and fallbacks
- Red text shows errors and failures
- All exports include source field and target key information
- Custom outputs are clearly marked with "Custom export:" prefix
