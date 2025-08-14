# Repeat Step Functionality

The repeat step allows you to execute a set of nested steps multiple times in your workflow. This is useful for scenarios where you need to perform the same operations multiple times with different parameters or for testing purposes.

## Overview

The repeat step is a new step type that:
- Executes a specified number of nested steps
- Provides iteration variables for dynamic value substitution
- Maintains the same workflow context across iterations
- Supports all existing step types as nested steps

## Basic Syntax

```yaml
- name: Repeat Operations
  type: repeat
  count: 3  # Number of times to repeat
  steps:    # Array of nested steps to execute
    - name: Step 1
      type: wait
      seconds: 1
    - name: Step 2
      type: call
      # ... other step configuration
```

## Available Iteration Variables

The repeat step provides several iteration variables that can be used in nested steps:

- `{{iteration}}` - Current iteration number (1-based, e.g., 1, 2, 3)
- `{{iteration_index}}` - Current iteration index (0-based, e.g., 0, 1, 2)
- `{{iteration_zero_based}}` - Same as iteration_index (0-based)
- `{{iteration_one_based}}` - Same as iteration (1-based)

## Use Cases

### 1. Simple Repetition
Execute the same operation multiple times:

```yaml
- name: Set Multiple Keys
  type: repeat
  count: 5
  steps:
    - name: Set Key-Value
      type: call
      node: calimero-node-1
      context_id: '{{context.calimero-node-1}}'
      method: set
      args:
        key: "key_{{iteration}}"
        value: "value_{{iteration}}"
```

### 2. Complex Operations
Execute multiple related operations in sequence, repeated multiple times:

```yaml
- name: Complex Operation Sequence
  type: repeat
  count: 3
  steps:
    - name: Set Data
      type: call
      node: calimero-node-1
      context_id: '{{context.calimero-node-1}}'
      method: set
      args:
        key: "data_{{iteration}}"
        value: "content_{{iteration}}"
    
    - name: Wait for Propagation
      type: wait
      seconds: 2
    
    - name: Verify Data
      type: call
      node: calimero-node-2
      context_id: '{{context.calimero-node-1}}'
      method: get
      args:
        key: "data_{{iteration}}"
```

### 3. Testing Scenarios
Test the same functionality multiple times to ensure consistency:

```yaml
- name: Test Consistency
  type: repeat
  count: 10
  steps:
    - name: Set Test Value
      type: call
      node: calimero-node-1
      context_id: '{{context.calimero-node-1}}'
      method: set
      args:
        key: "test_key"
        value: "test_value_{{iteration}}"
    
    - name: Get Test Value
      type: call
      node: calimero-node-2
      context_id: '{{context.calimero-node-1}}'
      method: get
      args:
        key: "test_key"
```

## Supported Nested Step Types

The repeat step supports all existing step types as nested steps:

- `install_application` - Install applications
- `create_context` - Create contexts
- `create_identity` - Generate identities
- `invite_identity` - Invite identities to contexts
- `join_context` - Join contexts
- `call` - Execute contract calls
- `wait` - Wait for specified time
- `repeat` - Nested repeat steps (recursive)

## Example Workflows

### Basic Example
See `workflow-example.yml` for a simple repeat step example.

### Comprehensive Example
See `workflow-repeat-example.yml` for multiple repeat step use cases.

## Testing

You can test the repeat step functionality using the provided test script:

```bash
python test_repeat_step.py
```

## Implementation Details

### Class Structure
- `RepeatStep` - Main repeat step executor class
- Inherits from `BaseStep` for consistency
- Supports iteration variables in dynamic values

### Execution Flow
1. Parse repeat configuration (count, nested steps)
2. For each iteration:
   - Create iteration-specific dynamic values
   - Execute all nested steps in sequence
   - Pass iteration variables to nested steps
3. Continue to next iteration or complete

### Error Handling
- If any nested step fails, the repeat step fails
- Detailed logging for each iteration and step
- Clear error messages with iteration context

## Best Practices

1. **Use Meaningful Names**: Give your repeat steps descriptive names
2. **Limit Iteration Count**: Avoid extremely high iteration counts that could impact performance
3. **Include Wait Steps**: Add appropriate wait steps between operations for state propagation
4. **Test Incrementally**: Start with small iteration counts and increase gradually
5. **Monitor Resources**: Be aware of resource usage with large iteration counts

## Limitations

- Nested steps share the same workflow context
- No built-in conditional logic within iterations
- All iterations must complete successfully for the repeat step to succeed
- No parallel execution of iterations (sequential only)

## Future Enhancements

Potential improvements for future versions:
- Conditional iteration execution
- Parallel iteration execution
- Dynamic iteration count based on workflow state
- Break/continue logic within iterations
- Performance metrics and optimization
