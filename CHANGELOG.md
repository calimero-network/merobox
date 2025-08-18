# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.7] - 2024-12-19

### Fixed
- **Version Consistency**: Ensure all version references are properly synchronized across all files
- **Build Artifacts**: Rebuild package with correct version numbers in all source files

### Technical Details
- Updated version to 0.1.7 in pyproject.toml, setup.py, merobox_cli.py, merobox/cli.py, and merobox/__init__.py
- Fixed version mismatch between package metadata and source code versions

## [0.1.6] - 2024-12-19

### Added
- **Script Step Functionality**: New generic `script` step type for workflow execution
- **Flexible Script Targeting**: Support for `target: "image"` (Docker image) and `target: "nodes"` (running nodes)
- **Workflow Reorganization**: Centralized workflow examples in `workflow-examples/` directory
- **Resource Organization**: Scripts moved to `workflow-examples/scripts/` and resources to `workflow-examples/res/`
- **Documentation Consolidation**: Single comprehensive README.md combining all previous documentation

### Changed
- **Refactored Script Execution**: Replaced separate `pre_script` and `post_script` flags with unified `script` step type
- **Enhanced Error Handling**: Improved workflow execution with immediate termination on failures
- **Updated Command Examples**: All examples now use `merobox` command directly instead of `python merobox_cli.py`
- **Improved File Structure**: Better organization of workflow examples and associated resources

### Technical Details
- New `ScriptStep` class in `commands/bootstrap/steps/script.py`
- Enhanced workflow executor with better error handling and script step support
- Removed legacy `execute_pre_script` and `execute_post_script_on_nodes` methods
- Updated all workflow YAML files to use new script step syntax
- Consolidated documentation into single README.md file
- Reorganized file structure for better maintainability

### Examples
- Script execution on Docker images before node startup
- Script execution on all running nodes after startup
- Unified script step configuration in workflow YAML
- Improved workflow organization and resource management

## [0.1.5] - 2024-01-XX

### Changed
- **Docker Image Update**: Updated merod image from specific commit to `latest` for better maintainability
- **Log Level Optimization**: Changed default RUST_LOG level from `debug` to `info` for production use

### Technical Details
- Updated Docker image reference in manager.py from `ghcr.io/calimero-network/merod:6a47604` to `ghcr.io/calimero-network/merod:latest`
- Modified RUST_LOG environment variable from `debug` to `info` for better performance and reduced log verbosity

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