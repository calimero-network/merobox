# Merobox

A Python CLI tool for managing Calimero nodes in Docker containers and executing workflows.

## Features

- **Node Management**: Start, stop, and manage Calimero nodes in Docker containers
- **Application Installation**: Install applications on Calimero nodes
- **Context Management**: Create and manage Calimero contexts
- **Identity Management**: Generate and manage identities for contexts
- **Workflow Execution**: Execute complex workflows defined in YAML files
- **Contract Execution**: Execute contract calls, view calls, and function calls
- **Health Monitoring**: Check the health status of running nodes

## Installation

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Ensure Docker is running

## Usage

### Basic Commands

```bash
# List running nodes
python merobox_cli.py list

# Start nodes
python merobox_cli.py run --count 2

# Check node health
python merobox_cli.py health

# Stop nodes
python merobox_cli.py stop
```

### Workflow Execution

Execute complex workflows defined in YAML files:

```bash
python merobox_cli.py bootstrap workflow-example.yml
```

### Contract Execution

Execute contract calls directly:

```bash
# Contract call
python merobox_cli.py execute \
  --rpc-url http://localhost:8080 \
  --context-id your-context-id \
  --type contract_call \
  --method set \
  --args '{"key": "hello", "value": "world"}' \
  --gas-limit 300000000000000

# View call (read-only)
python merobox_cli.py execute \
  --rpc-url http://localhost:8080 \
  --context-id your-context-id \
  --type view_call \
  --method get \
  --args '{"key": "hello"}'

# Function call
python merobox_cli.py execute \
  --rpc-url http://localhost:8080 \
  --context-id your-context-id \
  --type function_call \
  --method custom_function \
  --args '{"param1": "value1"}' \
  --gas-limit 500000000000000
```

## Workflow YAML Format

Workflows can include various step types:

```yaml
steps:
  # Install application
  - name: Install App
    type: install_application
    node: calimero-node-1
    path: ./app.wasm
    dev: true

  # Create context
  - name: Create Context
    type: create_context
    node: calimero-node-1
    application_id: '{{install.calimero-node-1}}'

  # Execute contract calls
  - name: Set Key-Value
    type: execute
    exec_type: contract_call
    node: calimero-node-1
    context_id: '{{context.calimero-node-1}}'
    method: set
    args:
      key: hello
      value: world
    gas_limit: 300000000000000

  - name: Get Value
    type: execute
    exec_type: view_call
    node: calimero-node-1
    context_id: '{{context.calimero-node-1}}'
    method: get
    args:
      key: hello
```

## Architecture

The tool is built with a modular architecture:

- **Commands**: Individual CLI commands for different operations
- **Manager**: Docker container management
- **WorkflowExecutor**: Workflow orchestration and execution
- **AdminClient**: Admin API operations (no authentication required)
- **JsonRpcClient**: JSON-RPC operations (requires authentication)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

[Add your license here]
