# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PyPI packaging configuration
- Modern Python packaging with pyproject.toml
- Development dependencies and tooling configuration

## [0.1.2] - 2024-01-XX

### Changed
- **Renamed `execute` command to `call`**: Improved command naming for better clarity
- **Updated workflow step type**: Changed from `type: execute` to `type: call` in YAML workflows
- **Enhanced command structure**: Better organized command modules and imports

### Technical Details
- Renamed `commands/execute.py` to `commands/call.py`
- Updated all CLI imports and references
- Updated bootstrap workflow system to use `call` step type
- Updated documentation and examples throughout
- Maintained backward compatibility in functionality

## [0.1.1] - 2024-01-XX

### Added
- Initial release of Merobox CLI
- Docker container management for Calimero nodes
- Application installation and management
- Context creation and management
- Identity generation and management
- Context invitation and joining
- Contract execution (contract calls, view calls, function calls)
- Automated workflow execution with bootstrap command
- Dynamic value capture and placeholder resolution
- Cross-node operations and data sharing

### Features
- **Node Management**: Start, stop, and manage multiple Calimero nodes
- **Application Management**: Install and manage WASM applications
- **Context Management**: Create and manage application contexts
- **Identity Management**: Generate and manage node identities
- **Access Control**: Invite and join contexts with proper permissions
- **Contract Execution**: Execute smart contract operations
- **Workflow Automation**: Define and execute multi-step workflows
- **Dynamic Values**: Capture and reuse values between workflow steps

### Technical Details
- Built with Click for CLI interface
- Rich for enhanced terminal output
- Docker SDK for container management
- Calimero client SDK integration
- YAML-based workflow configuration
- Modular command architecture
