# Merobox CLI

A comprehensive Python CLI tool for managing Calimero nodes in Docker containers and executing complex blockchain workflows.

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

## 📚 Documentation

- **[📖 Workflow Guide](docs/WORKFLOW_GUIDE.md)** - Complete guide to creating and executing workflows
- **[🔧 API Reference](docs/API_REFERENCE.md)** - All commands, options, and configuration
- **[🛠️ Development Guide](docs/DEVELOPMENT.md)** - Building, testing, and contributing
- **[❓ Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions

## ✨ Features

- **Node Management**: Start, stop, and monitor Calimero nodes in Docker
- **Workflow Orchestration**: Execute complex multi-step workflows with YAML
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Calls**: Execute smart contract functions via JSON-RPC
- **Dynamic Variables**: Advanced placeholder resolution with embedded support

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
├── docs/                       # Documentation
└── Makefile                   # Build automation
```

## 🛠️ Development

### Build and Test

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
```

### Release Process

```bash
# Build and check
make check

# Test publish to TestPyPI
make test-publish

# Publish to PyPI
make publish
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

See [Development Guide](docs/DEVELOPMENT.md) for detailed contribution instructions.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: Check the guides above
- **Examples**: See `workflow-examples/` directory
- **Issues**: [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- **Help**: `merobox --help` for command help

## 🔗 Quick Links

- **Workflows**: [Workflow Guide](docs/WORKFLOW_GUIDE.md)
- **Commands**: [API Reference](docs/API_REFERENCE.md)
- **Development**: [Development Guide](docs/DEVELOPMENT.md)
- **Troubleshooting**: [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
- **Examples**: `workflow-examples/` directory
- **Source**: [GitHub Repository](https://github.com/calimero-network/merobox)