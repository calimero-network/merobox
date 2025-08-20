# Merobox API Reference

This document provides a complete reference for all Merobox commands, options, and configuration.

## Table of Contents

- [Command Overview](#command-overview)
- [Core Commands](#core-commands)
- [Workflow Commands](#workflow-commands)
- [Node Management](#node-management)
- [Context & Identity Management](#context--identity-management)
- [Function Execution](#function-execution)
- [Utility Commands](#utility-commands)

## Command Overview

Merobox provides a comprehensive CLI for managing Calimero nodes and executing workflows:

```bash
merobox [OPTIONS] COMMAND [ARGS]...
```

### Global Options

- `-v, --verbose`: Enable verbose output
- `--version`: Show version and exit
- `--help`: Show help message and exit

## Core Commands

### `merobox run` - Start Calimero Nodes

Start Calimero nodes in Docker containers.

```bash
merobox run [OPTIONS]
```

**Options:**
- `-c, --count INTEGER`: Number of nodes to start (default: 1)
- `--base-port INTEGER`: Base P2P port for nodes (default: 2428)
- `--base-rpc-port INTEGER`: Base RPC port for nodes (default: 2528)
- `--image TEXT`: Docker image to use (default: ghcr.io/calimero-network/node:latest)
- `--data-dir PATH`: Data directory for nodes (default: ./data)

**Examples:**
```bash
# Start a single node
merobox run

# Start 3 nodes with custom ports
merobox run --count 3 --base-port 3000 --base-rpc-port 3100

# Use custom data directory
merobox run --data-dir /custom/path
```

### `merobox stop` - Stop Calimero Nodes

Stop running Calimero nodes.

```bash
merobox stop [OPTIONS]
```

**Options:**
- `--all`: Stop all running nodes
- `--node TEXT`: Stop specific node by name

**Examples:**
```bash
# Stop all nodes
merobox stop --all

# Stop specific node
merobox stop --node calimero-node-1
```

### `merobox list` - List Node Status

List all Calimero nodes and their status.

```bash
merobox list [OPTIONS]
```

**Options:**
- `--format TEXT`: Output format (table, json, yaml)

**Examples:**
```bash
# List in table format
merobox list

# List in JSON format
merobox list --format json
```

### `merobox health` - Check Node Health

Check health status of all Calimero nodes.

```bash
merobox health [OPTIONS]
```

**Options:**
- `--node TEXT`: Check specific node by name
- `--timeout INTEGER`: Health check timeout in seconds (default: 30)

**Examples:**
```bash
# Check all nodes
merobox health

# Check specific node
merobox health --node calimero-node-1
```

### `merobox logs` - View Node Logs

View logs for Calimero nodes.

```bash
merobox logs [OPTIONS] NODE_NAME
```

**Options:**
- `-f, --follow`: Follow log output
- `--tail INTEGER`: Number of lines to show (default: 100)
- `--since TEXT`: Show logs since timestamp

**Examples:**
```bash
# View recent logs
merobox logs calimero-node-1

# Follow logs in real-time
merobox logs -f calimero-node-1

# Show last 50 lines
merobox logs --tail 50 calimero-node-1
```

### `merobox nuke` - Remove All Nodes

Stop and remove all Calimero nodes and data.

```bash
merobox nuke [OPTIONS]
```

**Options:**
- `--force`: Skip confirmation prompt

**Examples:**
```bash
# Remove all nodes with confirmation
merobox nuke

# Force removal without confirmation
merobox nuke --force
```

## Workflow Commands

### `merobox bootstrap run` - Execute Workflows

Execute a Calimero workflow from a YAML configuration file.

```bash
merobox bootstrap run [OPTIONS] WORKFLOW_FILE
```

**Options:**
- `-v, --verbose`: Enable verbose output
- `--dry-run`: Validate workflow without executing
- `--stop-on-error`: Stop execution on first error

**Examples:**
```bash
# Execute workflow
merobox bootstrap run workflow.yml

# Execute with verbose output
merobox bootstrap run -v workflow.yml

# Validate without executing
merobox bootstrap run --dry-run workflow.yml
```

### `merobox bootstrap validate` - Validate Workflows

Validate a Calimero workflow YAML configuration file.

```bash
merobox bootstrap validate [OPTIONS] WORKFLOW_FILE
```

**Options:**
- `-v, --verbose`: Enable verbose output
- `--strict`: Enable strict validation mode

**Examples:**
```bash
# Validate workflow
merobox bootstrap validate workflow.yml

# Strict validation
merobox bootstrap validate --strict workflow.yml
```

### `merobox bootstrap create-sample` - Create Sample Workflow

Create a sample workflow configuration file.

```bash
merobox bootstrap create-sample [OPTIONS]
```

**Options:**
- `-o, --output PATH`: Output file path (default: workflow-sample.yml)

**Examples:**
```bash
# Create default sample
merobox bootstrap create-sample

# Create sample with custom name
merobox bootstrap create-sample -o my-workflow.yml
```

## Node Management

### `merobox install` - Install Applications

Install applications on Calimero nodes.

```bash
merobox install [OPTIONS]
```

**Options:**
- `--node TEXT`: Target node name (required)
- `--path PATH`: Local file path to install
- `--url TEXT`: Remote URL to install from
- `--dev`: Enable development mode
- `--output-format TEXT`: Output format (table, json, yaml)

**Examples:**
```bash
# Install from local file
merobox install --node calimero-node-1 --path ./app.wasm

# Install from URL
merobox install --node calimero-node-1 --url https://example.com/app.wasm

# Install in development mode
merobox install --node calimero-node-1 --path ./app.wasm --dev
```

## Context & Identity Management

### `merobox context` - Manage Contexts

Manage Calimero contexts.

```bash
merobox context [OPTIONS] COMMAND [ARGS]...
```

**Commands:**
- `create`: Create a new context
- `list`: List all contexts
- `info`: Show context information
- `delete`: Delete a context

**Examples:**
```bash
# Create context
merobox context create --node calimero-node-1 --app-id <app-id>

# List contexts
merobox context list --node calimero-node-1

# Show context info
merobox context info --node calimero-node-1 --context-id <context-id>
```

### `merobox identity` - Manage Identities

Manage Calimero identities.

```bash
merobox identity [OPTIONS] COMMAND [ARGS]...
```

**Commands:**
- `generate`: Generate new identity
- `list`: List all identities
- `info`: Show identity information
- `delete`: Delete an identity

**Examples:**
```bash
# Generate identity
merobox identity generate --node calimero-node-1

# List identities
merobox identity list --node calimero-node-1

# Show identity info
merobox identity info --node calimero-node-1 --identity-id <identity-id>
```

### `merobox join` - Join Contexts

Join Calimero contexts using invitations.

```bash
merobox join [OPTIONS] INVITATION
```

**Options:**
- `--node TEXT`: Target node name (required)
- `--output-format TEXT`: Output format (table, json, yaml)

**Examples:**
```bash
# Join context
merobox join --node calimero-node-2 <invitation-string>
```

## Function Execution

### `merobox call` - Execute Function Calls

Execute smart contract function calls.

```bash
merobox call [OPTIONS]
```

**Options:**
- `--node TEXT`: Target node name (required)
- `--context-id TEXT`: Context ID to execute in (required)
- `--method TEXT`: Function name to call (required)
- `--args TEXT`: Function arguments in JSON format
- `--executor-public-key TEXT`: Public key for execution
- `--exec-type TEXT`: Execution type (sync, async) (default: sync)
- `--output-format TEXT`: Output format (table, json, yaml)

**Examples:**
```bash
# Simple function call
merobox call --node calimero-node-1 --context-id <context-id> --method get

# Function call with arguments
merobox call --node calimero-node-1 --context-id <context-id> --method set --args '{"key": "test", "value": "data"}'

# Async execution
merobox call --node calimero-node-1 --context-id <context-id> --method process --exec-type async
```

## Utility Commands

### `merobox --help` - Show Help

Show help information for commands.

```bash
merobox --help
merobox COMMAND --help
```

### `merobox --version` - Show Version

Show Merobox version information.

```bash
merobox --version
```

## Configuration

### Environment Variables

Merobox respects these environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `MEROBOX_DATA_DIR` | Default data directory for nodes | `./data` |
| `MEROBOX_DOCKER_IMAGE` | Default Docker image | `ghcr.io/calimero-network/node:latest` |
| `MEROBOX_BASE_PORT` | Default base P2P port | `2428` |
| `MEROBOX_BASE_RPC_PORT` | Default base RPC port | `2528` |

### Configuration Files

Merobox can use configuration files for common settings:

```yaml
# ~/.merobox/config.yml
data_dir: /custom/data/path
docker_image: custom/calimero-node:latest
base_port: 3000
base_rpc_port: 3100
```

## Output Formats

Most commands support multiple output formats:

- **`table`** (default): Human-readable table format
- **`json`**: Machine-readable JSON format
- **`yaml`**: YAML format for configuration

## Error Handling

Merobox provides consistent error handling:

- **Exit Codes**: 0 for success, non-zero for errors
- **Error Messages**: Clear, actionable error descriptions
- **Suggestions**: Helpful suggestions for resolving issues

## Examples

### Complete Workflow Example

```bash
# 1. Start nodes
merobox run --count 2

# 2. Wait for nodes to be ready
sleep 10

# 3. Check node health
merobox health

# 4. Install application
merobox install --node calimero-node-1 --path ./app.wasm

# 5. Create context
merobox context create --node calimero-node-1 --app-id <app-id>

# 6. Execute function
merobox call --node calimero-node-1 --context-id <context-id> --method init

# 7. Stop nodes
merobox stop --all
```

### Batch Operations

```bash
# Start multiple nodes
merobox run --count 5 --base-port 4000

# Check all nodes health
merobox health

# View logs for all nodes
for node in calimero-node-{1..5}; do
  echo "=== $node ==="
  merobox logs --tail 10 $node
done
```

For more detailed examples and workflow configurations, see the [Workflow Guide](WORKFLOW_GUIDE.md).
