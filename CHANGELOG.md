# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.27] - 2025-10-18

### Added
- **GitHub Actions Workflow**: Automated release workflow for building binaries and publishing to PyPI
- **Multi-Platform Binaries**: Automated builds for macOS (x64, arm64) and Linux (x64, arm64)
- **Release Automation**: One-command release process via git tags

### Changed
- **Release Process**: Streamlined release workflow with GitHub Actions
- **Documentation**: Added comprehensive release setup guide

## [0.1.21] - 2024-12-19

### Changed
- **Version Bump**: Updated to version 0.1.21 for release

## [0.1.11] - 2024-12-19

### Added
- **Docker Image Force Pull**: New `force_pull_image` workflow configuration option
- **CLI Force Pull Flag**: `--force-pull` option for the `run` command
- **Automatic Image Management**: Smart Docker image pulling with remote detection
- **Image Pull Progress**: Real-time feedback during Docker image operations

### Changed
- **Image Handling**: Enhanced Docker image management with automatic remote detection
- **Workflow Configuration**: Added `force_pull_image` flag to workflow YAML files
- **Documentation**: Comprehensive documentation for Docker image management features

### Technical Details
- **Remote Detection**: Automatically identifies remote images (containing `/` and `:`)
- **Smart Pulling**: Only pulls images when necessary, with force pull override options
- **Error Handling**: Graceful fallback when image operations fail
- **Integration**: Seamlessly integrated into both CLI commands and workflow execution

## [0.1.10] - 2024-12-19

### Fixed
- **Documentation Distribution**: Fixed PyPI package to include docs/ folder with all documentation files
- **Package Structure**: Moved docs/ folder into merobox package for proper distribution
- **Documentation Links**: Updated README.md to reflect new documentation structure

## [0.1.9] - 2024-12-19

### Added
- **Bootstrap Command Refactoring**: Split bootstrap command into logical subcommands (run, validate, create-sample)
- **Modular Architecture**: Reorganized bootstrap steps into separate modules for better maintainability
- **Input Validation Framework**: Comprehensive validation for all step types with required field checking
- **Explicit Export Enforcement**: Variables are now only exported when explicitly configured in outputs
- **Code Formatting**: Added Black formatter with GitHub Actions CI integration
- **Documentation Reorganization**: Created docs/ folder with topic-specific documentation files

### Changed
- **Command Structure**: `merobox bootstrap` now requires subcommand (run, validate, create-sample)
- **Import Strategy**: Converted all imports to absolute imports for better package compatibility
- **Validation Logic**: Moved validation functions to dedicated validator module
- **Step Execution**: Separated workflow execution logic into dedicated run module
- **CLI Organization**: Better separation of concerns between command definition and execution logic

### Fixed
- **Import Paths**: Resolved dynamic import issues in bootstrap executor
- **Validation Errors**: Fixed missing field validation for all step types
- **Documentation Links**: Ensured PyPI compatibility for documentation structure

## [0.1.8] - 2024-12-19

### Added
- **PyPI Release**: Package now available on PyPI for easy installation
- **Makefile Automation**: Complete build and release automation using Makefile
- **Release Documentation**: Comprehensive release process documentation in README
- **Development Workflow**: Streamlined development and release process

### Changed
- **Package Structure**: Moved commands/ into merobox/commands/ for proper package layout
- **Build System**: Switched to setup.py for better metadata version control
- **CLI Entry Point**: Removed duplicate merobox_cli.py, using merobox/cli.py as canonical entry point
- **Documentation**: Consolidated all documentation into comprehensive README.md

### Fixed
- **Embedded Placeholders**: Fixed dynamic variable resolution for placeholders within strings (e.g., `complex_key_{{current_iteration}}_b`)
- **Variable Resolution**: Added recursive args processing for dynamic variables in ExecuteStep
- **Repeat Step Outputs**: Implemented custom outputs for repeat steps with proper iteration variable mapping
- **Metadata Compatibility**: Resolved PyPI upload issues by using compatible metadata version 2.1
- **Import Strategy**: CLI now supports both package and direct script execution

### Removed
- **Duplicate CLI**: Removed redundant merobox_cli.py entry point
- **Redundant Scripts**: Removed scripts/publish.py in favor of Makefile automation
- **pyproject.toml**: Simplified to use only setup.py for better compatibility

### Technical Details
- **Metadata Version**: Fixed from 2.4 to 2.1 for PyPI compatibility
- **Package Layout**: Standard Python package structure with merobox/commands/ subpackage
- **Build Commands**: `make build`, `make check`, `make publish` for streamlined workflow
- **Development Mode**: `make install-dev` for local development installation

## [0.1.7] - 2024-12-19

### Added
- **Dynamic Variable Resolution**: Support for placeholders like `{{variable_name}}` in workflow configurations
- **Workflow Orchestration**: Multi-step workflow execution with YAML configuration
- **Bootstrap System**: Automated workflow execution engine
- **Step Types**: Install, context, identity, invite, join, call, wait, and repeat steps
- **Embedded Placeholder Support**: Variables can be embedded within strings (e.g., `key_{{iteration}}_suffix`)

### Changed
- **Package Structure**: Reorganized commands into logical modules
- **CLI Framework**: Enhanced Click-based command-line interface
- **Documentation**: Added comprehensive workflow examples and usage guides

### Fixed
- **Variable Replacement**: Corrected logic for resolving dynamic values in workflow steps
- **Iteration Handling**: Fixed repeat step variable mapping and output processing
- **Args Processing**: Added recursive processing for dynamic values in function call arguments

## [0.1.6] - 2024-12-18

### Added
- **Workflow Support**: Basic workflow execution capabilities
- **Dynamic Variables**: Initial placeholder replacement system
- **Step Framework**: Extensible step execution architecture

### Changed
- **CLI Structure**: Reorganized command structure for better maintainability
- **Error Handling**: Improved error reporting and user feedback

## [0.1.5] - 2024-12-17

### Added
- **Context Management**: Create and manage blockchain contexts
- **Identity Management**: Generate and manage cryptographic identities
- **Function Execution**: Execute smart contract functions via JSON-RPC

### Changed
- **Node Communication**: Enhanced JSON-RPC client for better node interaction
- **Command Structure**: Reorganized CLI commands for logical grouping

## [0.1.4] - 2024-12-16

### Added
- **Multi-Node Support**: Start and manage multiple Calimero nodes
- **Port Management**: Automatic port detection and assignment
- **Health Monitoring**: Node health status checking

### Changed
- **Docker Integration**: Improved container management and monitoring
- **Error Handling**: Better error reporting and recovery

## [0.1.3] - 2024-12-15

### Added
- **Application Installation**: Install WASM applications on nodes
- **Log Management**: View and follow node logs
- **Data Cleanup**: Complete node data removal with nuke command

### Changed
- **CLI Interface**: Enhanced command-line interface with better help and options
- **Docker Management**: Improved container lifecycle management

## [0.1.2] - 2024-12-14

### Added
- **Basic Node Management**: Start, stop, and list Calimero nodes
- **Docker Integration**: Container-based node deployment
- **Configuration Management**: Customizable node settings

### Changed
- **Project Structure**: Initial package organization
- **Dependencies**: Added core dependencies for Docker and CLI operations

## [0.1.1] - 2024-12-13

### Added
- **Project Foundation**: Initial project setup and structure
- **Basic CLI Framework**: Click-based command-line interface foundation
- **Documentation**: Basic README and project documentation

## [0.1.0] - 2024-12-12

### Added
- **Initial Release**: Project creation and basic structure
- **License**: MIT License for open source development
- **Project Configuration**: Basic setup.py and project metadata