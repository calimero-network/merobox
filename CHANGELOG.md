# Changelog

All notable changes to the Merobox project will be documented in this file.

## [2.0.0] - 2024-12-19

### Added
- **Bootstrap Command**: New comprehensive workflow automation system
  - YAML-based workflow configuration
  - Automatic dynamic value capture and reuse
  - Support for complex multi-step operations
  - Built-in validation and error handling

- **Join Context Functionality**: New command to join contexts using invitations
  - `join context` command for joining contexts
  - Support for invitation-based context joining
  - Automatic payload format detection

- **Enhanced Execute Command**: Improved contract execution capabilities
  - Automatic executor public key detection from context
  - Support for multiple execution types (contract_call, view_call, function_call)
  - Enhanced error reporting and debugging information
  - Flexible argument handling for complex operations

- **Advanced Dynamic Value System**: Sophisticated placeholder resolution
  - Field-specific placeholders (e.g., `{{context.node.memberPublicKey}}`)
  - Complex nested placeholder resolution
  - Automatic data structure handling
  - Support for cross-step data flow

- **Comprehensive Workflow Steps**: New step types for complete automation
  - `install_application`: Install applications with automatic ID capture
  - `create_context`: Create contexts with member public key extraction
  - `create_identity`: Generate identities with public key capture
  - `invite_identity`: Invite identities with capability management
  - `join_context`: Join contexts using captured invitation data
  - `execute`: Execute functions with automatic executor detection
  - `wait`: Pause execution for specified duration

### Changed
- **CLI Structure**: Updated command structure for better organization
  - Bootstrap command integrated into main CLI
  - Improved command naming and organization
  - Better help text and documentation

- **Documentation**: Complete documentation overhaul
  - Updated README.md with all new features
  - Comprehensive BOOTSTRAP_README.md
  - Detailed workflow examples and tutorials
  - API reference and troubleshooting guides

- **Error Handling**: Enhanced error reporting and debugging
  - Detailed API response logging
  - Comprehensive error information
  - Better debugging information for complex operations

- **Node Management**: Improved Docker container management
  - Better node readiness detection
  - Enhanced port mapping handling
  - Improved error handling for container operations

### Improved
- **Dynamic Value Resolution**: More robust placeholder handling
  - Better error messages for missing values
  - Fallback handling for edge cases
  - Improved debugging information

- **Workflow Execution**: Enhanced workflow orchestration
  - Better step validation
  - Improved error propagation
  - Enhanced progress reporting

- **API Integration**: Better integration with Calimero APIs
  - Improved admin API client usage
  - Better JSON-RPC endpoint handling
  - Enhanced payload format support

### Fixed
- **Duplicate Steps**: Removed duplicate wait step in workflow example
- **Command Structure**: Fixed bootstrap command integration
- **Documentation**: Corrected outdated examples and references
- **Error Handling**: Fixed error propagation in workflow execution

## [1.0.0] - 2024-12-01

### Added
- Basic node management functionality
- Application installation capabilities
- Context and identity management
- Basic contract execution
- Health monitoring and logging

### Features
- Docker container management for Calimero nodes
- JSON-RPC and admin API integration
- Basic workflow execution
- Node health checking and monitoring

## Migration Guide

### From Version 1.x to 2.x

#### New Bootstrap Command
The new bootstrap command replaces manual step-by-step execution:

**Before (v1.x):**
```bash
# Manual execution of each step
python merobox_cli.py install --node calimero-node-1 --path ./app.wasm
python merobox_cli.py context create --node calimero-node-1 --application-id <app-id>
# ... more manual steps
```

**After (v2.x):**
```bash
# Single command with automated workflow
python merobox_cli.py bootstrap workflow.yml
```

#### Enhanced Execute Command
The execute command now automatically detects executor public keys:

**Before (v1.x):**
```bash
python merobox_cli.py execute \
  --rpc-url http://localhost:8080 \
  --context-id your-context-id \
  --type contract_call \
  --method set \
  --args '{"key": "hello", "value": "world"}'
```

**After (v2.x):**
```bash
python merobox_cli.py execute \
  --node calimero-node-1 \
  --context-id your-context-id \
  --function set \
  --args '{"key": "hello", "value": "world"}'
```

#### Dynamic Value System
New placeholder system for automatic value capture:

**Before (v1.x):**
```yaml
# Manual value copying required
application_id: manually-copied-app-id
context_id: manually-copied-context-id
```

**After (v2.x):**
```yaml
# Automatic value capture and reuse
application_id: '{{install.calimero-node-1}}'
context_id: '{{context.calimero-node-1}}'
```

## Breaking Changes

- **CLI Commands**: Some command options have changed for better consistency
- **Execute Command**: The `--rpc-url` option is now `--node` for better integration
- **Workflow Execution**: Manual step execution is replaced by the bootstrap command

## Deprecation Notices

- Manual step-by-step execution is deprecated in favor of workflow automation
- Direct RPC URL specification is deprecated in favor of node-based execution
- Basic workflow execution is deprecated in favor of the bootstrap command

## Future Roadmap

### Planned Features
- **Workflow Templates**: Pre-built workflow templates for common use cases
- **Conditional Execution**: Support for conditional steps and branching
- **Parallel Execution**: Support for parallel step execution
- **External Integrations**: Integration with external services and APIs
- **Monitoring and Alerting**: Enhanced monitoring and alerting capabilities

### Performance Improvements
- **Async Optimization**: Better async execution patterns
- **Resource Management**: Improved resource usage and cleanup
- **Caching**: Intelligent caching for frequently accessed data

### Developer Experience
- **Plugin System**: Extensible plugin architecture
- **Testing Framework**: Comprehensive testing and validation tools
- **CI/CD Integration**: Better integration with CI/CD pipelines
