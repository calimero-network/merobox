# Merobox

A powerful Python CLI tool for managing Calimero nodes in Docker containers and executing complex workflows with dynamic value capture and script execution capabilities.

## 🚀 Features

- **Node Management**: Start, stop, and manage Calimero nodes in Docker containers
- **Application Installation**: Install applications on Calimero nodes
- **Context Management**: Create and manage Calimero contexts
- **Identity Management**: Generate and manage identities for contexts
- **Workflow Execution**: Execute complex workflows defined in YAML files using the bootstrap command
- **Script Steps**: Execute custom scripts on Docker images or running nodes
- **Contract Execution**: Execute contract calls, view calls, and function calls
- **Health Monitoring**: Check the health status of running nodes
- **Context Joining**: Join contexts using invitations
- **Automated Workflows**: Complete automation of multi-step Calimero operations
- **Dynamic Value Capture**: Automatic capture and reuse of generated values
- **Repeat Steps**: Execute sets of operations multiple times with iteration variables

## 📦 Installation

### Option 1: Install from Source

```bash
# Clone the repository
git clone https://github.com/your-username/merobox.git
cd merobox

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install merobox in development mode
pip install -e .
```

### Option 2: Install from PyPI

```bash
pip install merobox
```

### Option 3: Install from Wheel

```bash
# Download the latest wheel from releases
pip install merobox-*.whl
```

### Prerequisites

- **Python 3.8+**
- **Docker** (running and accessible)
- **Docker Compose** (optional, for advanced setups)

## 🎯 Quick Start

### 1. Verify Installation

```bash
merobox --help
```

### 2. Start Your First Nodes

```bash
# Start 2 Calimero nodes
merobox run --count 2

# Check node status
merobox list

# Monitor node health
merobox health
```

### 3. Run a Sample Workflow

```bash
# Create a sample workflow
merobox bootstrap --create-sample

# Run the sample workflow
merobox bootstrap workflow-example.yml
```

## 📚 Usage

### Basic Commands

```bash
# List running nodes
merobox list

# Start nodes
merobox run --count 2

# Check node health
merobox health

# Stop nodes
merobox stop

# View node logs
merobox logs calimero-node-1
```

### Workflow Execution (Bootstrap)

Execute complex workflows defined in YAML files using the bootstrap command:

```bash
# Run a workflow
merobox bootstrap workflow-example.yml

# Create a sample workflow
merobox bootstrap --create-sample

# Run with verbose output
merobox bootstrap --verbose workflow-example.yml
```

### Application Management

```bash
# Install application on a node
merobox install --node calimero-node-1 --path ./app.wasm --dev

# Install from URL
merobox install --node calimero-node-1 --url https://example.com/app.wasm
```

### Context Management

```bash
# Create a context
merobox context create --node calimero-node-1 --application-id your-app-id

# List contexts
merobox context list --node calimero-node-1

# Get context details
merobox context get --node calimero-node-1 --context-id your-context-id
```

### Identity Management

```bash
# Generate identity
merobox identity generate --node calimero-node-1

# Invite identity to context
merobox identity invite \
  --node calimero-node-1 \
  --context-id your-context-id \
  --granter-id granter-public-key \
  --grantee-id grantee-public-key \
  --capability member
```

### Context Joining

```bash
# Join a context using invitation
merobox join context \
  --node calimero-node-2 \
  --context-id your-context-id \
  --invitee-id your-public-key \
  --invitation invitation-data
```

### Contract Execution

Execute contract calls directly:

```bash
# Contract call
merobox call \
  --node calimero-node-1 \
  --context-id your-context-id \
  --function set \
  --args '{"key": "hello", "value": "world"}'

# View call (read-only)
merobox call \
  --node calimero-node-1 \
  --context-id your-context-id \
  --function get \
  --args '{"key": "hello"}'
```

## 🔧 Workflow Configuration

### Basic Workflow Structure

```yaml
name: "My Calimero Workflow"
description: "Description of what this workflow does"

nodes:
  count: 2
  prefix: "calimero-node"
  image: "ghcr.io/calimero-network/merod:edge"

steps:
  - name: "Install Application"
    type: "install_application"
    node: "calimero-node-1"
    path: "./app.wasm"
    dev: true
```

### Node Configuration Options

#### Simple Multiple Nodes
```yaml
nodes:
  count: 2
  prefix: "calimero-node"
  chain_id: "testnet-1"
  image: "ghcr.io/calimero-network/merod:edge"
```

#### Individual Node Configuration
```yaml
nodes:
  node1:
    port: 2428
    rpc_port: 2528
    chain_id: "testnet-1"
    data_dir: "./data/custom-node1"
  node2:
    port: 2429
    rpc_port: 2529
    chain_id: "testnet-1"
```

### Node Management Flags

Control node behavior with two key flags:

```yaml
# Control node restart at beginning
restart: false

# Control node stopping at end
stop_all_nodes: false
```

#### Flag Combinations

| restart | stop_all_nodes | Behavior |
|---------|----------------|----------|
| `true` | `true` | Fresh start, complete cleanup |
| `false` | `false` | Reuse existing, leave running |
| `true` | `false` | Fresh start, leave running |
| `false` | `true` | Reuse existing, complete cleanup |

## 📝 Step Types

### Core Steps

#### `install_application`
Installs an application on a specified node.

```yaml
- name: "Install App"
  type: "install_application"
  node: "calimero-node-1"
  path: "./app.wasm"
  dev: true
  outputs:
    app_id: id                      # Export 'id' field as 'app_id'
```

**Dynamic Values Captured:**
- Application ID (stored as `{{app_id}}`)

#### `create_context`
Creates a context for an application.

```yaml
- name: "Create Context"
  type: "create_context"
  node: "calimero-node-1"
  application_id: "{{app_id}}"
  outputs:
    context_id: id                  # Export 'id' field as 'context_id'
    member_public_key: memberPublicKey  # Export 'memberPublicKey' as 'member_public_key'
```

**Dynamic Values Captured:**
- Context ID (stored as `{{context_id}}`)
- Member Public Key (accessible as `{{member_public_key}}`)

#### `create_identity`
Generates a new identity on a node.

```yaml
- name: "Generate Identity"
  type: "create_identity"
  node: "calimero-node-2"
  outputs:
    public_key: publicKey           # Export 'publicKey' as 'public_key'
```

**Dynamic Values Captured:**
- Public key (stored as `{{public_key}}`)

#### `invite_identity`
Invites an identity to join a context.

```yaml
- name: "Invite Identity"
  type: "invite_identity"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  granter_id: "{{member_public_key}}"
  grantee_id: "{{public_key}}"
  capability: "member"
  outputs:
    invitation: invitation           # Export 'invitation' as 'invitation'
```

**Dynamic Values Captured:**
- Invitation data (stored as `{{invitation}}`)

#### `join_context`
Joins a context using an invitation.

```yaml
- name: "Join Context"
  type: "join_context"
  node: "calimero-node-2"
  context_id: "{{context_id}}"
  invitee_id: "{{public_key}}"
  invitation: "{{invitation}}"
```

#### `call`
Executes contract calls, view calls, or function calls.

```yaml
- name: "Set Key-Value"
  type: "call"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  method: "set"
  args:
    key: "hello"
    value: "world"
  outputs:
    call_result: result             # Export 'result' as 'call_result'
```

**Features:**
- Automatically detects and uses the correct executor public key from the context
- Supports complex argument structures
- Comprehensive error reporting

#### `wait`
Pauses execution for a specified duration.

```yaml
- name: "Wait for Propagation"
  type: "wait"
  seconds: 5
```

### Advanced Steps

#### `script`
Execute custom scripts on Docker images or running nodes.

```yaml
- name: "Install Tools"
  type: "script"
  description: "Install curl and perf tools"
  script: "./workflow-examples/scripts/install-tools.sh"
  target: "image"  # Execute on Docker image before nodes start

- name: "Health Check"
  type: "script"
  description: "Verify node health"
  script: "./workflow-examples/scripts/health-check.sh"
  target: "nodes"  # Execute on all running nodes
```

**Target Types:**
- `"image"`: Execute on Docker image before nodes start
- `"nodes"`: Execute on all running Calimero nodes

#### `repeat`
Execute a set of nested steps multiple times.

```yaml
- name: "Repeat Operations"
  type: "repeat"
  count: 3
  steps:
    - name: "Set Key-Value"
      type: "call"
      node: "calimero-node-1"
      context_id: "{{context.calimero-node-1}}"
      method: "set"
      args:
        key: "key_{{iteration}}"
        value: "value_{{iteration}}"
    
    - name: "Wait for Propagation"
      type: "wait"
      seconds: 2
```

**Iteration Variables:**
- `{{iteration}}` - Current iteration number (1-based)
- `{{iteration_index}}` - Current iteration index (0-based)

## 🔄 Dynamic Values and Placeholders

The bootstrap command automatically captures dynamic values from each step and makes them available to subsequent steps using placeholders.

### Placeholder Format

```
{{type.node_name}}
{{type.node_name.field_name}}
```

### Available Placeholders

- `{{install.node_name}}` - Application ID from installation
- `{{context.node_name}}` - Context ID from context creation
- `{{context.node_name.memberPublicKey}}` - Member public key from context
- `{{identity.node_name}}` - Public key from identity generation
- `{{invite.node_name_identity.node_name}}` - Invitation data from invitation
- `{{iteration}}` - Current iteration number in repeat steps

### Example Workflow with Dynamic Values

```yaml
steps:
  # Install application
  - name: "Install App"
    type: "install_application"
    node: "calimero-node-1"
    path: "./app.wasm"
    dev: true
    outputs:
      app_id: id                      # Export 'id' field as 'app_id'

  # Create context using captured app ID
  - name: "Create Context"
    type: "create_context"
    node: "calimero-node-1"
    application_id: "{{app_id}}"
    outputs:
      context_id: id                  # Export 'id' field as 'context_id'
      member_public_key: memberPublicKey  # Export 'memberPublicKey' as 'member_public_key'

  # Generate identity
  - name: "Generate Identity"
    type: "create_identity"
    node: "calimero-node-2"
    outputs:
      public_key: publicKey           # Export 'publicKey' as 'public_key'

  # Invite using captured values
  - name: "Invite Identity"
    type: "invite_identity"
    node: "calimero-node-1"
    context_id: "{{context_id}}"
    granter_id: "{{member_public_key}}"
    grantee_id: "{{public_key}}"
    capability: "member"
    outputs:
      invitation: invitation           # Export 'invitation' as 'invitation'

  # Join using invitation
  - name: "Join Context"
    type: "join_context"
    node: "calimero-node-2"
    context_id: "{{context_id}}"
    invitee_id: "{{public_key}}"
    invitation: "{{invitation}}"

  # Execute contract calls
  - name: "Set Key-Value"
    type: "call"
    node: "calimero-node-1"
    context_id: "{{context_id}}"
    method: "set"
    args:
      key: "hello"
      value: "world"
    outputs:
      call_result: result             # Export 'result' as 'call_result'
```

## 📁 Workflow Examples

### Basic Workflow
See `workflow-examples/workflow-example.yml` for a complete workflow example.

### Script Steps Workflow
See `workflow-examples/workflow-script-test.yml` for script step examples.

### Repeat Steps Workflow
See `workflow-examples/workflow-repeat-example.yml` for repeat step examples.

### Node Management Workflow
See `workflow-examples/workflow-restart-example.yml` for node management examples.

## 🛠️ Script Steps

### Creating Scripts

Scripts can be written in any language supported by the container (typically bash):

```bash
#!/bin/bash
echo "🚀 Script execution started on $(hostname)"

# Check merod process
if pgrep -f merod > /dev/null; then
    echo "✅ merod process running"
else
    echo "❌ merod process not found"
    exit 1
fi

echo "✅ Script completed successfully"
```

### Script Requirements

#### Image Target Scripts
- **Permissions**: Scripts are mounted read-only
- **User**: Runs as default user (often non-root)
- **Package Installation**: May require root privileges
- **Cleanup**: Temporary containers are automatically cleaned up

#### Nodes Target Scripts
- **Permissions**: Scripts are copied with executable permissions (0o755)
- **User**: Runs as root in Calimero node containers
- **Access**: Full access to node data directories and processes
- **Cleanup**: Scripts are automatically removed after execution

### Install Tools Script (`workflow-examples/scripts/install-tools.sh`)

```bash
#!/bin/bash
echo "🚀 Installing tools on Docker image..."

# Detect package manager
if command -v apt-get &> /dev/null; then
    echo "📦 Using apt-get"
    apt-get update
    apt-get install -y curl perf
elif command -v yum &> /dev/null; then
    echo "📦 Using yum"
    yum install -y curl perf
else
    echo "⚠️  No supported package manager found"
    exit 1
fi

echo "✅ Tools installed successfully"
```

### Health Check Script (`workflow-examples/scripts/health-check.sh`)

```bash
#!/bin/bash
echo "🔍 Health check on $(hostname)"

# Check merod process
if pgrep -f merod > /dev/null; then
    echo "✅ merod process running"
else
    echo "❌ merod process not found"
    exit 1
fi

# Check data directory
if [ -d "/app/data" ]; then
    echo "✅ Data directory exists"
else
    echo "❌ Data directory missing"
    exit 1
fi

echo "✅ Health check passed"
```

## 🔍 Troubleshooting

### Common Issues

1. **Script Not Found**
   - Ensure script path is correct relative to workflow file
   - Use absolute paths if needed
   - Check file permissions

2. **Permission Denied**
   - Image target scripts may run as non-root user
   - Use `sudo` or run as root in container
   - Check file permissions and ownership

3. **Workflow Stops Unexpectedly**
   - Script failures stop the entire workflow
   - Check script exit codes
   - Review error messages in output

4. **Dynamic Values Not Working**
   - Verify step names match placeholder references
   - Check that previous steps completed successfully
   - Use `--verbose` flag for detailed output

### Debugging Tips

- Use `--verbose` flag for detailed execution information
- Check script output in workflow logs
- Test scripts independently in containers
- Verify script permissions and dependencies
- Use `merobox bootstrap validate <file>` to check configuration

## 🏗️ Architecture

The tool is built with a modular architecture:

- **Commands**: Individual CLI commands for different operations
- **Manager**: Docker container management
- **WorkflowExecutor**: Workflow orchestration and execution with dynamic value capture
- **Steps**: Modular step executors for different operation types
- **AdminClient**: Admin API operations (no authentication required)
- **JsonRpcClient**: JSON-RPC operations (requires authentication)

## 🎯 Key Features

### Bootstrap Command
- **Automated Workflows**: Execute multi-step operations with a single command
- **Dynamic Value Capture**: Automatic capture and reuse of generated values
- **Error Handling**: Comprehensive error handling and validation
- **Node Management**: Automatic node startup and readiness checking
- **Flexible Configuration**: Support for both simple and complex node configurations

### Script Steps
- **Flexibility**: Position scripts anywhere in workflow
- **Reusability**: Use same scripts in multiple workflows
- **Maintainability**: Centralized script management
- **Debugging**: Better error handling and logging
- **Integration**: Seamless workflow integration

### Repeat Steps
- **Iteration Support**: Execute operations multiple times
- **Dynamic Variables**: Use iteration variables in nested steps
- **Nested Steps**: Support for all step types as nested steps
- **Error Handling**: Comprehensive error handling across iterations

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

[Add your license here]

## 📚 Additional Resources

- **Changelog**: See `CHANGELOG.md` for version history
- **Publishing**: See `PUBLISHING.md` for release information
- **Workflow Examples**: See `workflow-examples/` directory for complete examples
- **Issues**: Report bugs and request features on GitHub
