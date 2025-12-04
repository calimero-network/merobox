"""
Configuration management for bootstrap workflows.
"""

import re
from typing import Any

import yaml

from merobox.commands.utils import console


def validate_variable_name(name: str) -> bool:
    """
    Validate variable name follows conventions.

    Rules:
    - Alphanumeric and underscore only
    - No reserved keywords
    - Must start with letter or underscore

    Args:
        name: Variable name to validate

    Returns:
        True if valid, False otherwise
    """
    # Reserved keywords that cannot be used as variable names
    reserved = {
        "env",
        "iteration",
        "current_iteration",
        "workflow_results",
        "dynamic_values",
        "global_variables",
        "local_variables",
        "true",
        "false",
        "null",
        "none",
    }

    # Check if name is reserved
    if name.lower() in reserved:
        return False

    # Check pattern: must start with letter or underscore, then alphanumeric or underscore
    pattern = r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    return bool(re.match(pattern, name))


def validate_workflow_variables(variables: dict[str, Any]) -> None:
    """
    Validate workflow variables structure and names.

    Args:
        variables: Dictionary of variables to validate

    Raises:
        ValueError: If validation fails
    """
    if not isinstance(variables, dict):
        raise ValueError("Workflow 'variables' must be a dictionary")

    for var_name, var_value in variables.items():
        if not validate_variable_name(var_name):
            raise ValueError(
                f"Invalid variable name '{var_name}'. "
                "Variable names must start with a letter or underscore, "
                "contain only alphanumeric characters and underscores, "
                "and not be reserved keywords."
            )

        # Variables can be strings, numbers, booleans, or null
        if not isinstance(var_value, (str, int, float, bool, type(None))):
            raise ValueError(
                f"Variable '{var_name}' has invalid type. "
                "Variables must be strings, numbers, booleans, or null."
            )


def load_workflow_config(
    config_path: str, validate_only: bool = False
) -> dict[str, Any]:
    """Load workflow configuration from YAML file."""
    try:
        with open(config_path) as file:
            config = yaml.safe_load(file)

        # Skip basic validation if this is just for validation purposes
        if not validate_only:
            # Validate required fields
            required_fields = ["name", "nodes"]
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required field: {field}")

        # Validate top-level variables if present
        if "variables" in config:
            validate_workflow_variables(config["variables"])

        # Set default values for orchestration configuration
        if "max_workflow_nesting" not in config:
            config["max_workflow_nesting"] = 5

        if "workflow_timeout" not in config:
            config["workflow_timeout"] = 3600  # 1 hour default timeout

        return config

    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Workflow configuration file not found: {config_path}"
        ) from e
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML format: {str(e)}") from e
    except Exception as e:
        raise ValueError(f"Failed to load configuration: {str(e)}") from e


def create_sample_workflow_config(output_path: str = "workflow-example.yml"):
    """Create a sample workflow configuration file."""
    sample_config = {
        "name": "Sample Calimero Workflow",
        "description": "A sample workflow that demonstrates the bootstrap functionality with dynamic value capture",
        # Nuke all data before starting workflow (complete cleanup)
        "nuke_on_start": False,
        # Nuke all data after completing workflow (complete cleanup)
        "nuke_on_end": False,
        "stop_all_nodes": True,  # Stop all existing nodes before starting
        "wait_timeout": 60,  # Wait up to 60 seconds for nodes to be ready
        "force_pull_image": False,  # Force pull Docker images even if they exist locally
        "auth_service": False,  # Enable authentication service with Traefik proxy
        # Custom Docker image for the auth service
        "auth_image": "ghcr.io/calimero-network/mero-auth:edge",
        # Set the RUST_LOG level for Calimero nodes (error, warn, info, debug, trace)
        "log_level": "debug",
        # Set the RUST_BACKTRACE level for Calimero nodes (0, 1, full)
        "rust_backtrace": "0",
        # Global workflow variables accessible to all steps
        "variables": {
            "environment": "development",
            "test_mode": True,
        },
        # Maximum nesting depth for workflow orchestration (default: 5)
        "max_workflow_nesting": 5,
        # Timeout for child workflow execution in seconds (default: 3600)
        "workflow_timeout": 3600,
        "nodes": {
            "count": 2,
            "prefix": "calimero-node",
            "chain_id": "testnet-1",
            "image": "ghcr.io/calimero-network/merod:6a47604",
        },
        "steps": [
            {
                "name": "Install Application on Node 1",
                "type": "install_application",
                "node": "calimero-node-1",
                "path": "./workflow-examples/res/kv_store.wasm",
                "dev": True,
                "outputs": {"app_id": "id"},
            },
            {
                "name": "Create Context on Node 1",
                "type": "create_context",
                "node": "calimero-node-1",
                "application_id": "{{app_id}}",
                "outputs": {"context_id": "id", "member_public_key": "memberPublicKey"},
            },
            {
                "name": "Create Identity on Node 2",
                "type": "create_identity",
                "node": "calimero-node-2",
                "outputs": {"public_key": "publicKey"},
            },
            {
                "name": "Invite Identity",
                "type": "invite_identity",
                "node": "calimero-node-1",
                "context_id": "{{context_id}}",
                "grantee_id": "{{public_key}}",
                "granter_id": "{{member_public_key}}",
                "capability": "member",
                "outputs": {"invitation": "invitation"},
            },
            {
                "name": "Join Context from Node 2",
                "type": "join_context",
                "node": "calimero-node-2",
                "context_id": "{{context_id}}",
                "invitee_id": "{{public_key}}",
                "invitation": "{{invitation}}",
            },
            {
                "name": "Execute Contract Call Example",
                "type": "call",
                "node": "calimero-node-1",
                "context_id": "{{context_id}}",
                "method": "set",
                "args": {"key": "hello", "value": "world"},
                "outputs": {"call_result": "result"},
            },
        ],
    }

    try:
        with open(output_path, "w") as file:
            yaml.dump(sample_config, file, default_flow_style=False, indent=2)

        console.print(
            f"[green]✓ Sample workflow configuration created: {output_path}[/green]"
        )
        console.print(
            "[yellow]Note: Dynamic values are automatically captured and used with placeholders[/yellow]"
        )
        console.print(
            "[yellow]Note: Use 'script' step type to execute scripts on Docker images or running nodes[/yellow]"
        )

    except Exception as e:
        console.print(f"[red]Failed to create sample configuration: {str(e)}[/red]")
