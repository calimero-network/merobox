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
│   ├── execute.py       # Contract execution step
│   ├── repeat.py        # Loop/repeat step
│   ├── wait.py          # Wait/delay step
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

### Execution Steps
- **call**: Execute contract functions
- **repeat**: Loop through step sequences
- **wait**: Add delays between steps
- **script**: Execute custom scripts

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
