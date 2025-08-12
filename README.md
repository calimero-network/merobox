# Merobox CLI

A comprehensive Python CLI tool for managing Calimero nodes in Docker containers, with support for application installation, context management, and blockchain operations.

## 🚀 Features

- **Node Management**: Start, stop, and manage multiple Calimero nodes
- **Application Installation**: Install applications from URLs or local files
- **Context Management**: Create, list, and manage blockchain contexts
- **Health Monitoring**: Check node health and status
- **Log Management**: View and manage node logs
- **Data Management**: Complete data reset and cleanup capabilities

## 📋 Prerequisites

- Python 3.8+
- Docker
- Linux/macOS (Docker support)

## 🛠️ Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd merobox
   ```

2. **Create virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 🎯 Quick Start

### Basic Node Operations

```bash
# Start a single node
python3 merobox_cli.py run

# Start multiple nodes
python3 merobox_cli.py run --count 3 --base-port 2428 --base-rpc-port 2528

# List running nodes
python3 merobox_cli.py list

# Check node health
python3 merobox_cli.py health calimero-node-1

# View node logs
python3 merobox_cli.py logs calimero-node-1 --tail 50

# Stop a specific node
python3 merobox_cli.py stop calimero-node-1

# Stop all nodes
python3 merobox_cli.py stop
```

## 📚 Command Reference

### Node Management

#### `run` - Start Calimero Nodes

Start one or more Calimero nodes in Docker containers.

```bash
python3 merobox_cli.py run [OPTIONS]
```

**Options:**
- `-c, --count INTEGER`: Number of nodes to run (default: 1)
- `-p, --base-port TEXT`: Base P2P port (auto-detect if not specified)
- `-r, --base-rpc-port TEXT`: Base RPC port (auto-detect if not specified)
- `--chain-id TEXT`: Chain ID (default: testnet-1)
- `--prefix TEXT`: Node name prefix (default: calimero-node)
- `--data-dir TEXT`: Custom data directory for single node
- `--image TEXT`: Custom Docker image to use

**Examples:**
```bash
# Start single node with default settings
python3 merobox_cli.py run

# Start 3 nodes with custom ports
python3 merobox_cli.py run --count 3 --base-port 2428 --base-rpc-port 2528

# Start node with custom data directory
python3 merobox_cli.py run --data-dir ./custom-data
```

#### `stop` - Stop Calimero Nodes

Stop running Calimero node containers.

```bash
python3 merobox_cli.py stop [NODE_NAME]
```

**Examples:**
```bash
# Stop specific node
python3 merobox_cli.py stop calimero-node-1

# Stop all nodes
python3 merobox_cli.py stop
```

#### `list` - List Running Nodes

Display information about all running Calimero nodes.

```bash
python3 merobox_cli.py list
```

**Output includes:**
- Node name and status
- Docker image
- P2P and RPC ports
- Chain ID
- Creation time

#### `health` - Check Node Health

Check the health status of a specific node.

```bash
python3 merobox_cli.py health NODE_NAME [OPTIONS]
```

**Options:**
- `--timeout INTEGER`: Health check timeout in seconds (default: 30)

**Example:**
```bash
python3 merobox_cli.py health calimero-node-1 --timeout 60
```

#### `logs` - View Node Logs

View logs from a specific Calimero node.

```bash
python3 merobox_cli.py logs NODE_NAME [OPTIONS]
```

**Options:**
- `--tail INTEGER`: Number of lines to show (default: 100)
- `--follow, -f`: Follow log output in real-time

**Examples:**
```bash
# View last 50 log lines
python3 merobox_cli.py logs calimero-node-1 --tail 50

# Follow logs in real-time
python3 merobox_cli.py logs calimero-node-1 --follow
```

### Application Management

#### `install` - Install Applications

Install applications on Calimero nodes using the admin API.

```bash
python3 merobox_cli.py install [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to install the application on (required)
- `--url TEXT`: URL to install the application from
- `--path TEXT`: Local path for dev installation
- `--dev`: Install as development application from local path
- `--metadata TEXT`: Application metadata (optional)
- `--timeout INTEGER`: Timeout in seconds for installation (default: 30)
- `-v, --verbose`: Show verbose output

**Examples:**
```bash
# Install from URL
python3 merobox_cli.py install --node calimero-node-1 \
  --url https://example.com/app.wasm

# Install development application from local file
python3 merobox_cli.py install --node calimero-node-1 \
  --path ./kv_store.wasm --dev

# Install with metadata
python3 merobox_cli.py install --node calimero-node-1 \
  --url https://example.com/app.wasm \
  --metadata '{"version": "1.0.0"}'
```

**Installation Methods:**

1. **Remote Installation**: Downloads and installs from a URL
2. **Development Installation**: Installs from a local file path
   - Copies file to container's data directory
   - Uses `/admin-api/install-dev-application` endpoint
   - Provides container path for server access

### Context Management

#### `context` - Manage Blockchain Contexts

Create and manage Calimero contexts for different blockchain networks.

```bash
python3 merobox_cli.py context [COMMAND] [OPTIONS]
```

**Commands:**

##### `create` - Create New Context

```bash
python3 merobox_cli.py context create [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to create the context on (required)
- `--application-id TEXT`: Application ID to create context for (required)
- `--timeout INTEGER`: Timeout in seconds (default: 30)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py context create \
  --node calimero-node-1 \
  --application-id nTRRyabT8YUbDsdjYXepzjwb1hd66PznGVotb5NEDwN
```

**Context Creation Details:**
- Uses `/admin-api/contexts` endpoint
- Always sets protocol to "near"
- Includes initialization parameters
- Returns context ID and member public key

##### `list-contexts` - List All Contexts

```bash
python3 merobox_cli.py context list-contexts [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to list contexts from (required)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py context list-contexts --node calimero-node-1
```

**Output includes:**
- Context ID
- Application ID
- Root Hash

##### `get` - Get Context Details

```bash
python3 merobox_cli.py context get [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to get context from (required)
- `--context-id TEXT`: Context ID to retrieve (required)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py context get \
  --node calimero-node-1 \
  --context-id 5Ej7PzSn1dThL2X4WohDTsE8pDLT54uFyJFYhPKdSgh9
```

##### `delete` - Delete Context

```bash
python3 merobox_cli.py context delete [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to delete context from (required)
- `--context-id TEXT`: Context ID to delete (required)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py context delete \
  --node calimero-node-1 \
  --context-id 3smThiGEhPxNSvELYubNg2dKxUr5dRin2LbmEWC881f7
```

#### `identity` - Manage Context Identities

List identities associated with blockchain contexts.

```bash
python3 merobox_cli.py identity [COMMAND] [OPTIONS]
```

**Commands:**

##### `list-identities` - List Context Identities ✅ **WORKING**

```bash
python3 merobox_cli.py identity list-identities [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to list identities from (required)
- `--context-id TEXT`: Context ID to list identities for (required)
- `-v, --verbose`: Show verbose output including API response structure

**Example:**
```bash
python3 merobox_cli.py identity list-identities \
  --node calimero-node-1 \
  --context-id 5Ej7PzSn1dThL2X4WohDTsE8pDLT54uFyJFYhPKdSgh9
```

**Output includes:**
- Identity ID
- Context ID
- Public Key (if available)
- Status

**Note:** Identities can only be listed per context, not globally across all contexts.

##### `create` - Create New Identity ⚠️ **EXPERIMENTAL - NOT SUPPORTED**

```bash
python3 merobox_cli.py identity create [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to create identity on (required)
- `--context-id TEXT`: Context ID to create identity for (required)
- `--type TEXT`: Identity type (default: relayer)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py identity create \
  --node calimero-node-1 \
  --context-id 5Ej7PzSn1dThL2X4WohDTsE8pDLT54uFyJFYhPKdSgh9 \
  --type relayer
```

**Note:** This command is experimental and currently not supported by the Calimero API. Identities appear to be created automatically when contexts are created. The command will attempt multiple API endpoints but is expected to fail.

##### `generate` - Generate New Identity ⚠️ **EXPERIMENTAL - NOT SUPPORTED**

```bash
python3 merobox_cli.py identity generate [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to generate identity on (required)
- `--context-id TEXT`: Context ID to generate identity for (required)
- `--type TEXT`: Identity type (default: relayer)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py identity generate \
  --node calimero-node-1 \
  --context-id 5Ej7PzSn1dThL2X4WohDTsE8pDLT54uFyJFYhPKdSgh9 \
  --type relayer
```

**Note:** This command is experimental and currently not supported by the Calimero API. Based on the source code structure, this command attempts to use the `generate_identity` method via JSON-RPC, but the method is not found. The command will attempt multiple API endpoints but is expected to fail.

##### `get` - Get Identity Details ⚠️ **EXPERIMENTAL - NOT SUPPORTED**

```bash
python3 merobox_cli.py identity get [OPTIONS]
```

**Options:**
- `-n, --node TEXT`: Node name to get identity from (required)
- `--context-id TEXT`: Context ID the identity belongs to (required)
- `--identity-id TEXT`: Identity ID to retrieve (required)
- `-v, --verbose`: Show verbose output

**Example:**
```bash
python3 merobox_cli.py identity get \
  --node calimero-node-1 \
  --context-id 5Ej7PzSn1dThL2X4WohDTsE8pDLT54uFyJFYhPKdSgh9 \
  --identity-id FD9eue2zwyvfRgtqJF1q6tHdj4NYmPEG9dbT3k1sUZR9
```

**Note:** This command is experimental and currently not supported by the Calimero API. Individual identity details cannot be retrieved through the available endpoints. Only listing identities per context is supported.

**Current API Support:**
- ✅ **List identities per context**: `/admin-api/contexts/{context_id}/identities` (GET)
- ❌ **Create identity**: Not supported via API
- ❌ **Generate identity**: Not supported via API (method "generate_identity" not found)
- ❌ **Get individual identity**: Not supported via API

**Recommendation:** Use only the `list-identities` command for production use. The `create`, `generate`, and `get` commands are provided for experimental purposes and API exploration.

### Data Management

#### `nuke` - Reset All Data

Delete all Calimero node data folders for complete reset.

```bash
python3 merobox_cli.py nuke [OPTIONS]
```

**Options:**
- `--dry-run`: Show what would be deleted without actually deleting
- `--force, -f`: Force deletion without confirmation prompt
- `-v, --verbose`: Show verbose output

**Examples:**
```bash
# Preview what would be deleted
python3 merobox_cli.py nuke --dry-run

# Force delete all data
python3 merobox_cli.py nuke --force

# Delete with verbose output
python3 merobox_cli.py nuke --force --verbose
```

**Features:**
- Automatically stops running nodes before deletion
- Shows data size and directory information
- Provides progress indicators
- Safe confirmation prompts (unless --force is used)

## 🔧 Configuration

### Node Configuration

Nodes are configured with sensible defaults:

- **Docker Image**: `ghcr.io/calimero-network/merod:6a47604`
- **Chain ID**: `testnet-1`
- **P2P Port**: `2428` (auto-incremented for multiple nodes)
- **RPC Port**: `2528` (auto-incremented for multiple nodes)
- **Data Directory**: `./data/{node-name}`

### Environment Variables

- `CALIMERO_HOME`: Container data directory (default: `/app/data`)
- `NODE_NAME`: Node identifier
- `RUST_LOG`: Logging level (default: `debug`)

## 📁 Project Structure

```
merobox/
├── commands/                 # CLI command modules
│   ├── __init__.py          # Command imports
│   ├── manager.py           # Docker container management
│   ├── run.py              # Node startup commands
│   ├── stop.py             # Node shutdown commands
│   ├── list.py             # Node listing commands
│   ├── health.py           # Health check commands
│   ├── logs.py             # Log management commands
│   ├── install.py          # Application installation
│   ├── context.py          # Context management
│   └── nuke.py             # Data cleanup commands
├── data/                    # Node data directories
├── externals/               # External dependencies
├── venv/                    # Python virtual environment
├── merobox_cli.py          # Main CLI entry point
├── example_usage.py         # Usage examples
└── README.md               # This documentation
```

## 🚨 Troubleshooting

### Common Issues

1. **Permission Denied Errors**
   - Some files may have restricted permissions
   - Use `--force` flag with nuke command
   - Check Docker container permissions

2. **Port Conflicts**
   - Use `--base-port` and `--base-rpc-port` options
   - Ensure ports are available on your system

3. **Container Startup Issues**
   - Check Docker daemon status
   - Verify image availability
   - Review container logs

4. **API Endpoint Errors**
   - Ensure node is fully started
   - Check RPC port accessibility
   - Verify admin API is enabled

### Debug Mode

Enable verbose output for detailed debugging:

```bash
# Most commands support --verbose flag
python3 merobox_cli.py run --verbose
python3 merobox_cli.py install --verbose
python3 merobox_cli.py context create --verbose
```

## 🔄 Complete Workflow Example

Here's a complete example of setting up and using Merobox:

```bash
# 1. Start fresh
python3 merobox_cli.py nuke --force

# 2. Start a node
python3 merobox_cli.py run --count 1 --base-port 2429 --base-rpc-port 2529

# 3. Wait for node to start and install application
sleep 10
python3 merobox_cli.py install --node calimero-node-1 \
  --url https://calimero-only-peers-dev.s3.amazonaws.com/uploads/e10f01cf6c6a72565fbd5aeb6a5e0860.wasm

# 4. Create context for the application
python3 merobox_cli.py context create \
  --node calimero-node-1 \
  --application-id nTRRyabT8YUbDsdjYXepzjwb1hd66PznGVotb5NEDwN

# 5. List all contexts
python3 merobox_cli.py context list-contexts --node calimero-node-1

# 6. Check node health
python3 merobox_cli.py health calimero-node-1

# 7. View logs
python3 merobox_cli.py logs calimero-node-1 --tail 20
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues and questions:

1. Check the troubleshooting section
2. Review existing issues
3. Create a new issue with detailed information
4. Include logs and error messages

## 🔗 Related Links

- [Calimero Network Documentation](https://docs.calimero.network/)
- [Docker Documentation](https://docs.docker.com/)
- [Python Click Documentation](https://click.palletsprojects.com/)

---

**Merobox CLI** - Your complete solution for managing Calimero blockchain nodes! 🚀
