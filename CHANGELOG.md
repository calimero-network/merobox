# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.4] - 2024-01-XX

### Changed
- **Node Management Behavior**: `restart` flag now controls node restart at the **beginning** of workflows, `stop_all_nodes` controls stopping at the **end** of workflows
- **Workflow Execution Flow**: Restart logic moved to beginning, cleanup logic moved to end for more logical behavior

### Features
- **Logical Node Lifecycle**: Clear separation between start and end node management
- **Improved Workflow Control**: Better control over when nodes are restarted vs. cleaned up
- **Enhanced Development Experience**: More efficient workflow reruns with node reuse options

### Technical Details
- Updated workflow executor to handle restart at beginning and cleanup at end
- Enhanced node management logic for better workflow control
- Improved logging and step descriptions for clearer execution flow
- Maintains backward compatibility with existing workflows

## [0.1.3] - 2024-01-XX

### Added
- **Repeat Step Functionality**: New workflow step type that executes nested steps multiple times
- **Iteration Variables**: Support for `{{iteration}}`, `{{iteration_index}}`, `{{iteration_zero_based}}`, and `{{iteration_one_based}}` placeholders
- **Nested Step Support**: All existing step types can be used within repeat steps
- **Comprehensive Documentation**: Added detailed README for repeat step functionality with examples
- **Restart Flag**: New `restart` configuration option to control whether nodes are restarted when rerunning workflows

### Changed
- **Node Management Behavior**: `restart` flag now controls node restart at the **beginning** of workflows, `stop_all_nodes` controls stopping at the **end** of workflows

### Features
- **Repeat Step Type**: Execute a set of nested steps for a specified number of iterations
- **Dynamic Value Substitution**: Use iteration variables in nested step configurations
- **Sequential Execution**: Steps execute in order for each iteration with proper error handling
- **Recursive Support**: Repeat steps can contain other repeat steps for complex workflows
- **Smart Node Management**: Control node restart behavior with `restart` and `stop_all_nodes` flags
- **Efficient Workflow Reruns**: Reuse existing running nodes to avoid unnecessary restarts
- **Logical Node Lifecycle**: `restart` controls beginning behavior, `stop_all_nodes` controls end behavior

### Technical Details
- New `RepeatStep` class in `commands/bootstrap/steps.py`
- Enhanced dynamic value resolution for iteration placeholders
- Updated executor to handle repeat step type
- Comprehensive workflow examples demonstrating various use cases
- Maintains backward compatibility with existing workflows
- Enhanced node management with restart flag support
- Improved workflow executor with smart node reuse logic
- Updated workflow execution flow: restart at beginning, stop at end

### Examples
- Simple repetition of operations
- Complex multi-step sequences
- Testing scenarios with multiple iterations
- Batch operations with different parameters
- Efficient workflow reruns without node restarts
- Selective node restart for specific workflows
- Logical node lifecycle management

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