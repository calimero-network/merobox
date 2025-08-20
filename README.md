# Merobox CLI

A comprehensive Python CLI tool for managing Calimero nodes in Docker containers and executing complex blockchain workflows.

## Features

- **Node Management**: Start, stop, and monitor Calimero nodes in Docker containers
- **Workflow Orchestration**: Execute complex multi-step workflows with YAML configuration
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Calls**: Execute smart contract functions via JSON-RPC
- **Dynamic Variables**: Advanced placeholder resolution with embedded variable support

## Installation

### From PyPI
```bash
pip install merobox
```

### From Source
```bash
git clone <repository-url>
cd merobox
pip install -e .
```

### Development Installation
```bash
git clone <repository-url>
cd merobox
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e .
```

## Quick Start

### 1. Start Calimero Nodes
```bash
# Start a single node
merobox run

# Start multiple nodes
merobox run --count 3

# Start with custom ports
merobox run --base-port 3000 --base-rpc-port 3100
```

### 2. List Running Nodes
```bash
merobox list
```

### 3. Check Node Health
```bash
merobox health
```

### 4. Execute Workflows
```bash
# Run a workflow from YAML file
merobox bootstrap workflow.yml

# Create a sample workflow
merobox bootstrap --create-sample
```

### 5. Execute Function Calls
```bash
merobox call --node calimero-node-1 --context-id <context-id> --function get --args '{"key": "example"}'
```

## Development and Release

### Package Management with Makefile

The project uses a Makefile for all package management tasks:

```bash
# Build the package
make build

# Check package with twine
make check

# Publish to PyPI (requires confirmation)
make publish

# Install in development mode
make install-dev

# Clean build artifacts
make clean

# Show all available commands
make help
```

### Release Process

1. **Update version** in `merobox/__init__.py` and `setup.py`
2. **Build and check** the package:
   ```bash
   make check
   ```
3. **Release to PyPI**:
   ```bash
   make publish
   ```
4. **Verify release** at [https://pypi.org/project/merobox/](https://pypi.org/project/merobox/)

### Development Workflow

```bash
# Install development dependencies
make install-dev

# Make changes to code
# ...

# Build and test package
make build
make check

# Install locally for testing
make install

# When ready to release
make publish
```

## Command Reference

### Node Management

#### `merobox run`
Start Calimero node(s) in Docker containers.

**Options:**
- `--count, -c`: Number of nodes to run (default: 1)
- `--base-port, -p`: Base P2P port (auto-detect if not specified)
- `--base-rpc-port, -r`: Base RPC port (auto-detect if not specified)
- `--chain-id`: Chain ID (default: testnet-1)
- `--prefix`: Node name prefix (default: calimero-node)
- `--data-dir`: Custom data directory for single node
- `--image`: Custom Docker image to use

#### `merobox stop`
Stop Calimero node(s).

**Options:**
- `--node`: Specific node name to stop
- `--all`: Stop all running nodes

#### `merobox list`
List all running Calimero nodes with their status and ports.

#### `merobox logs`
Show logs from a specific node.

**Options:**
- `--node`: Node name (required)
- `--follow, -f`: Follow log output

#### `merobox health`
Check the health status of all running Calimero nodes.

#### `merobox nuke`
Delete all Calimero node data folders for complete cleanup.

### Context Management

#### `merobox context`
Manage Calimero contexts for different blockchain environments.

**Subcommands:**
- `create`: Create a new context
- `list`: List contexts for a node
- `delete`: Delete a context

### Identity Management

#### `merobox identity`
Manage Calimero identities for contexts.

**Subcommands:**
- `create`: Create a new identity
- `list`: List identities for a node

### Application Management

#### `merobox install`
Install applications on Calimero nodes.

**Options:**
- `--node`: Target node name (required)
- `--path`: Path to application file (required)

### Function Execution

#### `merobox call`
Execute function calls on deployed applications.

**Options:**
- `--node`: Node name to execute on (required)
- `--context-id`: Context ID to execute in (required)
- `--function`: Function name to call (required)
- `--args`: JSON string of function arguments

### Context Joining

#### `merobox join context`
Join a context using an invitation.

**Options:**
- `--node`: Node name (required)
- `--invitation`: Invitation string (required)

### Workflow Orchestration

#### `merobox bootstrap`
Execute complex workflows from YAML configuration files.

**Options:**
- `--verbose, -v`: Enable verbose output
- `--create-sample`: Create a sample workflow configuration file

## Workflow Configuration

Workflows are defined in YAML files and support complex orchestration patterns:

### Basic Workflow Structure
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

### Supported Step Types

#### `install`
Install applications on nodes.
```yaml
- name: "Install App"
  type: "install"
  config:
    node: "calimero-node-1"
    path: "./app.wasm"
```

#### `context`
Create blockchain contexts.
```yaml
- name: "Create Context"
  type: "context"
  config:
    node: "calimero-node-1"
    application_id: "{{app_id}}"
```

#### `identity`
Create cryptographic identities.
```yaml
- name: "Create Identity"
  type: "identity"
  config:
    node: "calimero-node-2"
```

#### `invite`
Send context invitations.
```yaml
- name: "Invite Node"
  type: "invite"
  config:
    node: "calimero-node-1"
    context_id: "{{context_id}}"
    invitee_id: "{{public_key}}"
```

#### `join`
Join contexts using invitations.
```yaml
- name: "Join Context"
  type: "join"
  config:
    node: "calimero-node-2"
    invitation: "{{invitation}}"
```

#### `call`
Execute function calls.
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
```

#### `wait`
Add delays between steps.
```yaml
- name: "Wait"
  type: "wait"
  config:
    duration: 10
```

#### `repeat`
Execute nested steps multiple times.
```yaml
- name: "Repeat Operations"
  type: "repeat"
  config:
    iterations: 3
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

### Dynamic Variables

The workflow system supports powerful dynamic variable resolution:

**Important**: Variables are NOT automatically exported. They must be explicitly specified in the `outputs` configuration of each step.

#### Built-in Variables
- `{{iteration}}`: Current iteration number (1-based)
- `{{iteration_index}}`: Current iteration index (0-based)
- `{{iteration_zero_based}}`: Same as iteration_index
- `{{iteration_one_based}}`: Same as iteration

#### Custom Variables
Define custom variable mappings in step outputs:
```yaml
outputs:
  app_id: "applicationId"
  context_id: "contextId"
  public_key: "publicKey"
```

#### Embedded Placeholders
Variables can be embedded within strings:
```yaml
args:
  key: "complex_key_{{current_iteration}}_suffix"
  value: "data_for_iteration_{{current_iteration}}"
```

#### Explicit Export Configuration
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

## Examples

### Simple Node Setup
```bash
# Start 2 nodes
merobox run --count 2

# Check status
merobox list
merobox health

# Stop all nodes
merobox stop --all
```

### Workflow Execution
```bash
# Create sample workflow
merobox bootstrap --create-sample

# Execute workflow
merobox bootstrap workflow.yml

# Execute with verbose output
merobox bootstrap -v workflow.yml
```

### Manual Operations
```bash
# Install application
merobox install --node calimero-node-1 --path ./app.wasm

# Create context
merobox context create --node calimero-node-1 --app-id <app-id>

# Execute function
merobox call --node calimero-node-1 --context-id <context-id> --function get --args '{"key": "test"}'
```

## Advanced Workflow Patterns

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
      iterations: 5
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

## Development

### Project Structure
```
merobox/
├── merobox/
│   ├── cli.py              # Main CLI entry point
│   └── commands/           # Command implementations
│       ├── run.py          # Node management
│       ├── call.py         # Function execution
│       ├── bootstrap/      # Workflow orchestration
│       │   ├── executor.py # Workflow execution engine
│       │   └── steps/      # Step implementations
│       └── ...
├── workflow-examples/      # Example workflows
├── requirements.txt       # Dependencies
├── setup.py              # Package configuration
└── Makefile              # Build and release automation
```

### Running Tests
```bash
# Install in development mode
make install-dev

# Run example workflows
merobox bootstrap workflow-examples/workflow-example.yml
```

### Building from Source
```bash
# Build package
make build

# Install locally
make install
```

## Docker Requirements

Merobox requires Docker to be installed and running:

- **Docker Engine**: 20.10+ recommended
- **Docker Compose**: Optional, for complex setups
- **Permissions**: User must have Docker access

### Docker Setup

#### Linux
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
```

#### macOS
```bash
# Install Docker Desktop
brew install --cask docker
```

#### Windows
Download and install Docker Desktop from [docker.com](https://www.docker.com/products/docker-desktop).

## Troubleshooting

### Common Issues

#### Docker Permission Denied
```bash
# Add user to docker group (Linux)
sudo usermod -aG docker $USER
# Log out and back in
```

#### Port Conflicts
```bash
# Use custom ports
merobox run --base-port 4000 --base-rpc-port 4100
```

#### Node Startup Issues
```bash
# Check Docker status
docker ps

# View node logs
merobox logs --node calimero-node-1

# Clean restart
merobox nuke
merobox run
```

#### Workflow Failures
```bash
# Run with verbose output
merobox bootstrap -v workflow.yml

# Check node health
merobox health

# Review logs
merobox logs --node <node-name>
```

### Error Output Format

Errors are displayed in a consistent format:
```
[red]Error: [/red]Descriptive error message
[yellow]Suggestion: [/yellow]Helpful suggestion for resolution
```

## Environment Variables

Merobox respects these environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `MEROBOX_DATA_DIR` | Default data directory for nodes | `./data` |
| `MEROBOX_DOCKER_IMAGE` | Default Docker image | `ghcr.io/calimero-network/node:latest` |
| `MEROBOX_BASE_PORT` | Default base P2P port | `2428` |
| `MEROBOX_BASE_RPC_PORT` | Default base RPC port | `2528` |

## Performance Considerations

### Resource Usage

- **Memory**: Each node requires ~100-500MB RAM
- **Storage**: Each node requires ~50-100MB disk space
- **CPU**: Moderate CPU usage during operation
- **Network**: P2P and RPC ports per node

### Scaling Limits

- **Max nodes**: Limited by available ports and system resources
- **Concurrent operations**: Commands are generally synchronous
- **Workflow complexity**: Large workflows may require significant time

### Optimization Tips

1. Use `--base-port` to avoid port conflicts
2. Clean up unused nodes with `merobox stop --all`
3. Use `merobox nuke` to free disk space
4. Monitor system resources during large workflows

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For issues and questions:
- GitHub Issues: Report bugs and request features
- Documentation: Check this README and inline help (`merobox --help`)
- Examples: See `workflow-examples/` directory