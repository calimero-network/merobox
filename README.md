# Merobox CLI

A comprehensive Python CLI tool for managing Calimero nodes in Docker containers and executing complex blockchain workflows.

## 📚 Table of Contents

- [🚀 Quick Start](#-quick-start)
- [✨ Features](#-features)
- [📖 Workflow Guide](#-workflow-guide)
- [🔧 API Reference](#-api-reference)
- [🛠️ Development Guide](#️-development-guide)
- [❓ Troubleshooting](#-troubleshooting)
- [🏗️ Project Structure](#️-project-structure)
- [📋 Requirements](#-requirements)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🆘 Support](#-support)

## 🚀 Quick Start

### Installation

```bash
# From PyPI
pip install merobox

# From source
git clone https://github.com/calimero-network/merobox.git
cd merobox
pip install -e .
```

### Basic Usage

```bash
# Start Calimero nodes
merobox run --count 2

# Check node status
merobox list
merobox health

# Execute a workflow
merobox bootstrap run workflow.yml

# Stop all nodes
merobox stop --all
```

## ✨ Features

- **Node Management**: Start, stop, and monitor Calimero nodes in Docker
- **Workflow Orchestration**: Execute complex multi-step workflows with YAML
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Calls**: Execute smart contract functions via JSON-RPC
- **Dynamic Variables**: Advanced placeholder resolution with embedded support

---

## 📖 Workflow Guide

### Overview

Merobox workflows are defined in YAML files and executed through the `bootstrap` command. Workflows can include multiple steps like installing applications, creating contexts, managing identities, and executing function calls.

### Workflow Structure

```yaml
name: "Sample Workflow"
nodes:
  - calimero-node-1
  - calimero-node-2

steps:
  - name: "Install Application"
    type: "install"
    node: "calimero-node-1"
    path: "./app.wasm"
    outputs:
      applicationId: "app_id"
```

### Step Types

#### Install Step
Installs WASM applications on Calimero nodes.

```yaml
- name: "Install App"
  type: "install"
  node: "calimero-node-1"
  path: "./application.wasm"  # Local path
  # OR
  url: "https://example.com/app.wasm"  # Remote URL
  dev: true  # Development mode
  outputs:
    applicationId: "app_id"
```

#### Context Step
Creates blockchain contexts for applications.

```yaml
- name: "Create Context"
  type: "context"
  node: "calimero-node-1"
  application_id: "{{app_id}}"
  params:
    param1: "value1"
  outputs:
    contextId: "context_id"
    memberPublicKey: "member_key"
```

#### Identity Step
Generates cryptographic identities.

```yaml
- name: "Create Identity"
  type: "identity"
  node: "calimero-node-2"
  outputs:
    publicKey: "public_key"
```

#### Invite Step
Invites identities to join contexts.

```yaml
- name: "Invite Identity"
  type: "invite"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  grantee_id: "{{public_key}}"
  outputs:
    invitation: "invitation_data"
```

#### Join Step
Joins contexts using invitations.

```yaml
- name: "Join Context"
  type: "join"
  node: "calimero-node-2"
  context_id: "{{context_id}}"
  invitee_id: "{{public_key}}"
  invitation: "{{invitation_data}}"
```

#### Execute Step
Executes smart contract functions.

```yaml
- name: "Call Function"
  type: "call"
  node: "calimero-node-1"
  context_id: "{{context_id}}"
  method: "set"
  args:
    key: "hello"
    value: "world"
  executor_public_key: "{{member_key}}"
  outputs:
    result: "function_result"
```

#### Wait Step
Adds delays between steps.

```yaml
- name: "Wait"
  type: "wait"
  seconds: 5
```

#### Repeat Step
Executes steps multiple times.

```yaml
- name: "Repeat Operations"
  type: "repeat"
  count: 3
  steps:
    - name: "Set Value"
      type: "call"
      node: "calimero-node-1"
      context_id: "{{context_id}}"
      method: "set"
      args:
        key: "iteration_{{current_iteration}}"
        value: "value_{{current_iteration}}"
      executor_public_key: "{{member_key}}"
      outputs:
        result: "iteration_result"
    - name: "Wait"
      type: "wait"
      seconds: 2
  outputs:
    iteration: "current_iteration"
```

### Dynamic Variables

Workflows support dynamic variable substitution using `{{variable_name}}` syntax.

#### Variable Sources
- **Step Outputs**: Variables exported by previous steps
- **Workflow Context**: Global workflow variables
- **Environment**: System environment variables

#### Embedded Variables
Variables can be embedded within strings:

```yaml
args:
  key: "user_{{user_id}}_data_{{iteration}}"
```

#### Variable Resolution
- Variables are resolved at execution time
- Missing variables cause workflow failures
- Use `outputs` sections to export variables for later use

### Output Configuration

Each step can export variables for use in subsequent steps:

```yaml
outputs:
  variableName: "export_name"  # Maps API response field to export name
```

### Example Workflow

See `workflow-examples/workflow-example.yml` for a complete example.

---

## 🔧 API Reference

### Command Overview

```bash
merobox [OPTIONS] COMMAND [ARGS]...
```

### Global Options

- `--version`: Show version and exit
- `--help`: Show help message and exit

### Core Commands

#### `merobox run`
Start Calimero nodes.

```bash
merobox run [OPTIONS]
```

**Options:**
- `--count INTEGER`: Number of nodes to start (default: 1)
- `--prefix TEXT`: Node name prefix (default: "calimero-node")
- `--restart`: Restart existing nodes
- `--image TEXT`: Custom Docker image to use
- `--force-pull`: Force pull Docker image even if it exists locally
- `--help`: Show help message

#### `merobox stop`
Stop Calimero nodes.

```bash
merobox stop [OPTIONS]
```

**Options:**
- `--all`: Stop all running nodes
- `--prefix TEXT`: Stop nodes with specific prefix
- `--help`: Show help message

#### `merobox list`
List running Calimero nodes.

```bash
merobox list [OPTIONS]
```

**Options:**
- `--help`: Show help message

#### `merobox health`
Check health status of nodes.

```bash
merobox health [OPTIONS]
```

**Options:**
- `--help`: Show help message

#### `merobox logs`
View node logs.

```bash
merobox logs [OPTIONS] NODE_NAME
```

**Options:**
- `--follow`: Follow log output
- `--help`: Show help message

#### `merobox bootstrap`
Execute workflows and validate configurations.

```bash
merobox bootstrap [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**
- `run <config_file>`: Execute a workflow
- `validate <config_file>`: Validate workflow configuration
- `create-sample`: Create a sample workflow file

**Options:**
- `--help`: Show help message

#### `merobox install`
Install applications on nodes.

```bash
merobox install [OPTIONS] NODE_NAME PATH_OR_URL
```

**Options:**
- `--dev`: Development mode installation
- `--help`: Show help message

#### `merobox context`
Manage blockchain contexts.

```bash
merobox context [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**
- `create`: Create a new context
- `list`: List contexts
- `show`: Show context details

#### `merobox identity`
Manage cryptographic identities.

```bash
merobox identity [OPTIONS] COMMAND [ARGS]...
```

**Subcommands:**
- `generate`: Generate new identity
- `list`: List identities
- `show`: Show identity details

#### `merobox call`
Execute smart contract functions.

```bash
merobox call [OPTIONS] NODE_NAME CONTEXT_ID METHOD [ARGS]...
```

**Options:**
- `--executor-key TEXT`: Executor public key
- `--exec-type TEXT`: Execution type
- `--help`: Show help message

#### `merobox join`
Join blockchain contexts.

```bash
merobox join [OPTIONS] NODE_NAME CONTEXT_ID INVITEE_ID INVITATION
```

**Options:**
- `--help`: Show help message

#### `merobox nuke`
Remove all node data and containers.

```bash
merobox nuke [OPTIONS]
```

**Options:**
- `--help`: Show help message

### Configuration Files

#### Workflow Configuration
Workflows are defined in YAML files with the following structure:

```yaml
name: "Workflow Name"
# Force pull Docker images even if they exist locally
force_pull_image: false

nodes:
  - "node-name-1"
  - "node-name-2"

steps:
  - name: "Step Name"
    type: "step_type"
    # ... step-specific configuration

stop_all_nodes: true  # Optional: stop nodes after completion
```

**Configuration Options:**
- `force_pull_image`: When set to `true`, forces Docker to pull fresh images from registries, even if they exist locally. Useful for ensuring latest versions or during development.

### Docker Image Management

Merobox provides automatic Docker image management to ensure your workflows always have the required images:

#### **Automatic Image Pulling**
- **Remote Detection**: Automatically detects when images are from remote registries
- **Smart Pulling**: Only pulls images that aren't available locally
- **Progress Display**: Shows real-time pull progress and status

#### **Force Pull Options**
1. **CLI Flag**: Use `--force-pull` with the `run` command for individual operations
   ```bash
   merobox run --image ghcr.io/calimero-network/merod:edge --force-pull
   ```

2. **Workflow Configuration**: Set `force_pull_image: true` in your workflow YAML
   ```yaml
   name: "My Workflow"
   force_pull_image: true  # Will force pull all images
   nodes:
     image: ghcr.io/calimero-network/merod:edge
   ```

#### **Use Cases**
- **Development**: Always get latest images during development
- **Testing**: Ensure consistent image versions across environments
- **CI/CD**: Force fresh pulls in automated workflows
- **Production**: Update images without manual intervention

#### Environment Variables
- `CALIMERO_IMAGE`: Docker image for Calimero nodes
- `DOCKER_HOST`: Docker daemon connection string
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)

---

## 🛠️ Development Guide

### Testing with Merobox

Merobox can be used as a lightweight test harness for your Python projects. Use the built-in helpers in `merobox.testing` to spin up ephemeral Calimero nodes for integration tests and tear them down automatically.

#### Basic Cluster Management

**Context manager:**
```python
from merobox.testing import cluster

with cluster(count=2, prefix="ci", image="ghcr.io/calimero-network/merod:edge") as env:
    # env["nodes"] -> ["ci-1", "ci-2"]
    # env["endpoints"]["ci-1"] -> http://localhost:<rpc_port>
    ...  # call your code against the endpoints
```

**Pytest fixture:**
```python
# conftest.py
from merobox.testing import pytest_cluster

merobox_cluster = pytest_cluster(count=2, scope="session")

# test_example.py
def test_something(merobox_cluster):
    endpoints = merobox_cluster["endpoints"]
    assert len(endpoints) == 2
```

#### Workflow-based Pretest Setup

For more complex test scenarios, you can run entire Merobox workflows as pretest setup:

**Context manager:**
```python
from merobox.testing import workflow

with workflow("workflow-examples/workflow-example.yml", prefix="pretest") as env:
    # env["workflow_result"] -> True/False (workflow execution success)
    # env["nodes"] -> List of nodes created by the workflow
    # env["endpoints"] -> RPC endpoints for each node
    # env["manager"] -> CalimeroManager instance
    
    # Your test logic here
    # The workflow environment is automatically cleaned up on exit
```

**Pytest fixture:**
```python
# conftest.py
from merobox.testing import pytest_workflow

merobox_workflow = pytest_workflow(
    workflow_path="workflow-examples/workflow-example.yml",
    prefix="pretest",
    scope="session"
)

# test_example.py
def test_with_workflow_setup(merobox_workflow):
    workflow_result = merobox_workflow["workflow_result"]
    assert workflow_result is True
    
    nodes = merobox_workflow["nodes"]
    endpoints = merobox_workflow["endpoints"]
    # ... your test logic
```

**Options for workflow testing:**
- `workflow_path`: Path to the workflow YAML file
- `prefix`: Node name prefix filter
- `image`: Custom Docker image
- `chain_id`: Blockchain chain ID
- `wait_for_ready`: Whether to wait for nodes to be ready
- `scope`: Pytest fixture scope (function, class, module, session)

See `testing-examples/` for runnable examples including workflow pretest setup.

### Environment Setup

#### Prerequisites
- Python 3.8+
- Docker 20.10+
- Git

#### Local Development
```bash
# Clone repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

#### Development Dependencies
```bash
pip install -r requirements.txt
```

### Project Structure

```
merobox/
├── merobox/                    # Main package
│   ├── __init__.py            # Package initialization
│   ├── cli.py                 # CLI entry point
│   └── commands/              # Command implementations
│       ├── __init__.py        # Commands package
│       ├── manager.py         # Docker node management
│       ├── run.py             # Node startup
│       ├── stop.py            # Node shutdown
│       ├── list.py            # Node listing
│       ├── health.py          # Health checking
│       ├── logs.py            # Log viewing
│       ├── install.py         # Application installation
│       ├── context.py         # Context management
│       ├── identity.py        # Identity management
│       ├── call.py            # Function execution
│       ├── join.py            # Context joining
│       ├── nuke.py            # Data cleanup
│       ├── utils.py           # Utility functions
│       └── bootstrap/         # Workflow orchestration
│           ├── __init__.py
│           ├── bootstrap.py   # Main bootstrap command
│           ├── config.py      # Configuration loading
│           ├── run/           # Workflow execution
│           │   ├── __init__.py
│           │   ├── executor.py # Workflow executor
│           │   └── run.py     # Execution logic
│           ├── steps/         # Step implementations
│           │   ├── __init__.py
│           │   ├── base.py    # Base step class
│           │   ├── install.py # Install step
│           │   ├── context.py # Context step
│           │   ├── identity.py # Identity step
│           │   ├── execute.py # Execute step
│           │   ├── join.py    # Join step
│           │   ├── wait.py    # Wait step
│           │   ├── repeat.py  # Repeat step
│           │   └── script.py  # Script step
│           └── validate/      # Validation logic
│               ├── __init__.py
│               └── validator.py
├── workflow-examples/          # Example workflows
├── requirements.txt            # Python dependencies
├── setup.py                   # Package configuration
├── Makefile                   # Build automation
├── README.md                  # This file
└── LICENSE                    # MIT License
```

### Building and Testing

#### Build Commands
```bash
# Show all available commands
make help

# Build package
make build

# Check package
make check

# Install in development mode
make install

# Format code
make format

# Check formatting
make format-check
```

#### Testing
```bash
# Run tests (when implemented)
make test

# Run specific test file
python -m pytest tests/test_specific.py
```

#### Code Quality
```bash
# Format code with Black
make format

# Check formatting
make format-check

# Lint code (when implemented)
make lint
```

### Adding New Commands

1. Create command file in `merobox/commands/`
2. Implement Click command function
3. Add import to `merobox/commands/__init__.py`
4. Update `__all__` list
5. Test with `python3 merobox/cli.py --help`

### Adding New Step Types

1. Create step file in `merobox/commands/bootstrap/steps/`
2. Inherit from `BaseStep`
3. Implement required methods:
   - `_get_required_fields()`
   - `_validate_field_types()`
   - `execute()`
4. Add step type mapping in executor
5. Update validation logic

### Release Process

#### Version Management
- Update version in `merobox/__init__.py`
- Update version in `merobox/cli.py`
- Update version in `setup.py`
- Add entry to `CHANGELOG.md`

#### Publishing
```bash
# Build and check
make check

# Test publish to TestPyPI
make test-publish

# Publish to PyPI
make publish
```

#### Release Checklist
- [ ] All tests pass
- [ ] Documentation updated
- [ ] Version bumped
- [ ] Changelog updated
- [ ] Package builds successfully
- [ ] Package validates with twine
- [ ] Published to PyPI

---

## ❓ Troubleshooting

### Common Issues

#### Node Startup Problems

**Issue**: Nodes fail to start
```bash
Error: Failed to start Calimero node
```

**Solutions**:
1. Check Docker is running: `docker ps`
2. Verify port availability: `netstat -tulpn | grep :2528`
3. Check Docker permissions: `docker run hello-world`
4. Clean up existing containers: `merobox nuke`

**Issue**: Port conflicts
```bash
Error: Port 2528 already in use
```

**Solutions**:
1. Stop conflicting services: `lsof -ti:2528 | xargs kill`
2. Use different ports: `merobox run --count 1`
3. Clean up: `merobox stop --all`

#### Workflow Execution Issues

**Issue**: Dynamic variable resolution fails
```bash
Error: Variable '{{missing_var}}' not found
```

**Solutions**:
1. Check variable names in workflow
2. Verify previous steps export variables
3. Use `merobox bootstrap validate` to check configuration
4. Check variable naming consistency

**Issue**: Step validation fails
```bash
Error: Required field 'node' missing
```

**Solutions**:
1. Validate workflow: `merobox bootstrap validate workflow.yml`
2. Check step configuration
3. Verify required fields are present
4. Check field types and values

**Issue**: API calls fail
```bash
Error: API request failed
```

**Solutions**:
1. Check node health: `merobox health`
2. Verify node is ready: `merobox list`
3. Check network connectivity
4. Verify API endpoints

#### Docker Issues

**Issue**: Container creation fails
```bash
Error: Failed to create container
```

**Solutions**:
1. Check Docker daemon: `docker info`
2. Verify image exists: `docker images calimero/calimero`
3. Check disk space: `df -h`
4. Restart Docker: `sudo systemctl restart docker`

**Issue**: Container networking problems
```bash
Error: Network connection failed
```

**Solutions**:
1. Check Docker network: `docker network ls`
2. Verify container networking: `docker inspect <container>`
3. Check firewall settings
4. Restart Docker networking

#### Performance Issues

**Issue**: Slow workflow execution
```bash
Workflow taking longer than expected
```

**Solutions**:
1. Check node resources: `docker stats`
2. Monitor system resources: `htop`, `iotop`
3. Optimize workflow steps
4. Use appropriate wait times

**Issue**: High memory usage
```bash
Container using excessive memory
```

**Solutions**:
1. Check memory limits: `docker stats`
2. Monitor memory usage: `free -h`
3. Restart nodes if needed
4. Check for memory leaks

### Debugging

#### Enable Debug Logging
```bash
export LOG_LEVEL=DEBUG
merobox bootstrap run workflow.yml
```

#### Verbose Output
```bash
merobox bootstrap run --verbose workflow.yml
```

#### Check Node Logs
```bash
merobox logs <node_name> --follow
```

#### Inspect Containers
```bash
docker exec -it <container_name> /bin/sh
docker inspect <container_name>
```

#### Network Diagnostics
```bash
# Check container networking
docker network inspect bridge

# Test connectivity
docker exec <container> ping <target>

# Check port binding
netstat -tulpn | grep :2528
```

### Getting Help

1. **Check Documentation**: Review relevant sections above
2. **Validate Workflows**: Use `merobox bootstrap validate`
3. **Check Logs**: Review node and application logs
4. **Community Support**: [GitHub Issues](https://github.com/calimero-network/merobox/issues)
5. **Command Help**: `merobox --help` or `merobox <command> --help`

---

## 🏗️ Project Structure

```
merobox/
├── merobox/                    # Main package
│   ├── cli.py                 # CLI entry point
│   └── commands/              # Command implementations
│       ├── bootstrap/         # Workflow orchestration
│       ├── run.py             # Node management
│       ├── call.py            # Function execution
│       └── ...                # Other commands
├── workflow-examples/          # Example workflows
├── Makefile                   # Build automation
└── README.md                  # This comprehensive documentation
```

## 📋 Requirements

- **Python**: 3.8+
- **Docker**: 20.10+ for Calimero nodes
- **OS**: Linux, macOS, Windows

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

See the [Development Guide](#️-development-guide) section above for detailed contribution instructions.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: This comprehensive README
- **Examples**: See `workflow-examples/` directory
- **Issues**: [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- **Help**: `merobox --help` for command help

## 🔗 Quick Links

- **[🚀 Quick Start](#-quick-start)**
- **[📖 Workflow Guide](#-workflow-guide)**
- **[🔧 API Reference](#-api-reference)**
- **[🛠️ Development Guide](#️-development-guide)**
- **[❓ Troubleshooting](#-troubleshooting)**
- **[Examples](workflow-examples/) directory**
- **[Source](https://github.com/calimero-network/merobox)**