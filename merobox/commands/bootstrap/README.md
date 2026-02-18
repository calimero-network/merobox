# Bootstrap Command

The bootstrap command provides a comprehensive workflow orchestration system for Calimero nodes.

## Structure

```
bootstrap/
├── __init__.py          # Package initialization and exports
├── bootstrap.py         # Main CLI command with subcommands
├── config.py            # Configuration loading and management
├── run/                 # Workflow execution module
│   ├── __init__.py      # Module exports
│   ├── executor.py      # Workflow execution engine
│   └── run.py          # Workflow runner functions
├── validate/            # Validation module
│   ├── __init__.py      # Module exports
│   └── validator.py     # Configuration validation logic
├── steps/               # Individual step implementations
│   ├── __init__.py      # Step class exports
│   ├── base.py          # Base step class with validation
│   ├── install.py       # Application installation step
│   ├── context.py       # Context creation step
│   ├── identity.py      # Identity management steps
│   ├── join.py          # Context joining step
│   ├── invite_open.py   # Open invitation creation step
│   ├── join_open.py     # Join via open invitation step
│   ├── execute.py       # Contract execution step
│   ├── parallel.py      # Parallel execution step with failure modes
│   ├── repeat.py        # Loop/repeat step
│   ├── wait.py          # Wait/delay step
│   ├── wait_for_sync.py # Wait for node sync verification step
│   └── script.py        # Script execution step
└── README.md            # This file
```

## Architecture

### Modular Design
The bootstrap command is organized into logical modules:

- **`run/`**: Handles workflow execution, node management, and step processing
- **`validate/`**: Provides comprehensive configuration validation
- **`steps/`**: Contains individual step implementations with validation
- **`config/`**: Manages workflow configuration loading and parsing

### Main CLI Interface
The `bootstrap.py` file provides the Click command group that:
- Imports functionality from the modules
- Defines the CLI interface
- Handles command routing and error handling

## Commands

### `bootstrap run <config_file>`
Execute a complete workflow from YAML configuration.

**Features:**
- Node management (start/restart)
- Step-by-step execution
- Dynamic variable resolution
- Error handling and rollback
- Result capture and export

### `bootstrap validate <config_file>`
Validate workflow configuration without execution.

**Validation includes:**
- Required field checking
- Field type validation
- Step-specific validation
- Configuration structure validation

### `bootstrap create-sample`
Generate a sample workflow configuration file.

## Step Types

### Core Steps
- **install_application**: Install WASM applications
- **create_context**: Create Calimero contexts
- **create_identity**: Generate node identities
- **invite_identity**: Invite nodes to contexts
- **join_context**: Join existing contexts
- **invite_open**: Create open invitations (multiple participants can join)
- **join_open**: Join contexts using open invitations

### Execution Steps
- **call**: Execute contract functions
- **repeat**: Loop through step sequences
- **parallel**: Execute multiple step groups concurrently (see [Parallel Execution](#parallel-execution))
- **wait**: Add delays between steps
- **wait_for_sync**: Wait for nodes to reach consensus (root hash verification)
- **script**: Execute custom scripts

## Parallel Execution

The `parallel` step type allows executing multiple step groups concurrently with configurable error handling.

### Failure Modes

The `failure_mode` configuration option controls how failures are handled:

| Mode | Default | Behavior |
|------|---------|----------|
| `fail-slow` | **Yes** | Wait for all groups to complete, then return failure if any group failed |
| `fail-fast` | No | Immediately cancel all other running groups when one fails |
| `continue-on-error` | No | Wait for all groups, return success if at least one succeeded |

### Default Behavior

**The default mode is `fail-slow`**, which maintains backward compatibility. If no `failure_mode` is specified, all parallel groups will run to completion before reporting any failures.

### Configuration Example

```yaml
- name: Parallel Operations
  type: parallel
  failure_mode: fail-fast        # Error handling (fail-fast, fail-slow, continue-on-error)
  groups:
    - name: Group1
      count: 10                  # Number of iterations for this group
      steps:
        - type: call
          node: node1
          method: operation1
    - name: Group2
      steps:
        - type: call
          node: node2
          method: operation2
  outputs:
    group1_duration: Group1_duration_seconds
    overall_time: overall_duration_seconds
```

### Exported Variables

| Variable | Type | Description |
|----------|------|-------------|
| `group_count` | int | Total number of groups executed |
| `parallel_success_count` | int | Number of groups that completed successfully |
| `parallel_failure_count` | int | Number of groups that failed |
| `overall_duration_seconds` | float | Total execution time for all groups |
| `overall_duration_ms` | float | Total execution time in milliseconds |
| `{group_name}_duration_seconds` | float | Duration for each named group |

### Use Cases

- **fail-slow**: Use when you want all operations to attempt execution (e.g., data collection from multiple sources)
- **fail-fast**: Use when early termination is desired to save resources (e.g., critical operations where any failure is fatal)
- **continue-on-error**: Use for resilient workflows where partial success is acceptable (e.g., optional operations)

## Configuration

Workflows are defined in YAML files with:
- **nodes**: Node configuration (count, image, chain_id)
- **steps**: Ordered list of workflow steps
- **options**: Workflow behavior settings

Each step includes:
- **type**: Step type identifier
- **name**: Human-readable step name
- **config**: Step-specific configuration
- **outputs**: Variable export configuration

## Usage Examples

```bash
# Validate a workflow before running
merobox bootstrap validate my-workflow.yml

# Run a validated workflow
merobox bootstrap run my-workflow.yml

# Create a sample workflow to start with
merobox bootstrap create-sample
```

## Development

### Adding New Steps
1. Create a new step class in `steps/`
2. Inherit from `BaseStep`
3. Implement required methods:
   - `_get_required_fields()`
   - `_validate_field_types()`
   - `_get_exportable_variables()`
   - `execute()`
4. Add to `steps/__init__.py`
5. Add validation logic in `validate/validator.py`

### Adding New Validation Rules
1. Update `validate/validator.py`
2. Add validation logic to appropriate functions
3. Update tests to cover new validation rules

### Modifying Execution Logic
1. Update `run/executor.py` for workflow-level changes
2. Update `run/run.py` for execution flow changes
3. Update individual step classes for step-specific changes
