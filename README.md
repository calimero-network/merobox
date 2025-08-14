# Merobox

A Python CLI tool for managing Calimero nodes in Docker containers and executing workflows.

## Features

- **Node Management**: Start, stop, and manage Calimero nodes in Docker containers
- **Application Installation**: Install applications on Calimero nodes
- **Context Management**: Create and manage Calimero contexts
- **Identity Management**: Generate and manage identities for contexts
- **Workflow Execution**: Execute complex workflows defined in YAML files using the bootstrap command
- **Contract Execution**: Execute contract calls, view calls, and function calls
- **Health Monitoring**: Check the health status of running nodes
- **Context Joining**: Join contexts using invitations
- **Automated Workflows**: Complete automation of multi-step Calimero operations

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

### Workflow Execution (Bootstrap)

Execute complex workflows defined in YAML files using the bootstrap command:

```bash
# Run a workflow
python merobox_cli.py bootstrap workflow-example.yml

# Create a sample workflow
python merobox_cli.py bootstrap create-sample

# Validate workflow configuration
python merobox_cli.py bootstrap validate workflow-example.yml
```

### Application Management

```bash
# Install application on a node
python merobox_cli.py install --node calimero-node-1 --path ./app.wasm --dev

# Install from URL
python merobox_cli.py install --node calimero-node-1 --url https://example.com/app.wasm
```

### Context Management

```bash
# Create a context
python merobox_cli.py context create --node calimero-node-1 --application-id your-app-id

# List contexts
python merobox_cli.py context list --node calimero-node-1

# Get context details
python merobox_cli.py context get --node calimero-node-1 --context-id your-context-id
```

### Identity Management

```bash
# Generate identity
python merobox_cli.py identity generate --node calimero-node-1

# Invite identity to context
python merobox_cli.py identity invite \
  --node calimero-node-1 \
  --context-id your-context-id \
  --granter-id granter-public-key \
  --grantee-id grantee-public-key \
  --capability member
```

### Context Joining

```bash
# Join a context using invitation
python merobox_cli.py join context \
  --node calimero-node-2 \
  --context-id your-context-id \
  --invitee-id your-public-key \
  --invitation invitation-data
```

### Contract Execution

Execute contract calls directly:

```bash
# Contract call
python merobox_cli.py call \
  --node calimero-node-1 \
  --context-id your-context-id \
  --function set \
  --args '{"key": "hello", "value": "world"}'

# View call (read-only)
python merobox_cli.py call \
  --node calimero-node-1 \
  --context-id your-context-id \
  --function get \
  --args '{"key": "hello"}'
```

## Workflow YAML Format

Workflows can include various step types with automatic dynamic value capture:

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
    type: call
    node: calimero-node-1
    method: set
    args:
      key: hello
      value: world

  # Execute view calls
  - name: Get Value
    type: call
    node: calimero-node-2
    method: get
    args:
      key: hello
```

## Dynamic Values and Placeholders

The bootstrap command automatically captures dynamic values from each step and makes them available to subsequent steps using placeholders:

- `{{install.node_name}}` - Application ID from installation
- `{{context.node_name}}` - Context ID from context creation
- `{{identity.node_name}}` - Public key from identity generation
- `{{invite.node_name_identity.node_name}}` - Invitation data from invitation

## Architecture

The tool is built with a modular architecture:

- **Commands**: Individual CLI commands for different operations
- **Manager**: Docker container management
- **WorkflowExecutor**: Workflow orchestration and execution with dynamic value capture
- **AdminClient**: Admin API operations (no authentication required)
- **JsonRpcClient**: JSON-RPC operations (requires authentication)

## Key Features

### Bootstrap Command
- **Automated Workflows**: Execute multi-step operations with a single command
- **Dynamic Value Capture**: Automatic capture and reuse of generated values
- **Error Handling**: Comprehensive error handling and validation
- **Node Management**: Automatic node startup and readiness checking
- **Flexible Configuration**: Support for both simple and complex node configurations

### Enhanced Execute Command
- **Multiple Execution Types**: Support for contract calls, view calls, and function calls
- **Automatic Executor Detection**: Automatically detects and uses the correct executor public key
- **Flexible Argument Handling**: Support for complex argument structures
- **Comprehensive Error Reporting**: Detailed error information and debugging

### Context Management
- **Full CRUD Operations**: Create, read, update, and delete contexts
- **Admin API Integration**: Direct integration with Calimero admin APIs
- **Validation**: Comprehensive input validation and error handling

### Identity Management
- **Secure Generation**: Cryptographically secure identity generation
- **Invitation System**: Comprehensive invitation and capability management
- **Multi-Node Support**: Support for cross-node identity operations

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

[Add your license here]
