"""
Configuration management for bootstrap workflows.
"""

import os
import re
from typing import Any

import yaml

from merobox.commands.utils import console

# Pattern to match ${ENV_VAR} or ${ENV_VAR:-default} syntax
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env_vars(value: Any) -> Any:
    """
    Recursively expand ${ENV_VAR} placeholders in strings.

    Supports:
    - ${VAR} - expands to environment variable value
    - ${VAR:-default} - expands to default if VAR is not set

    Args:
        value: The value to expand (can be str, dict, list, or other)

    Returns:
        The value with environment variables expanded
    """
    if isinstance(value, str):

        def replace_env_var(match: re.Match) -> str:
            var_name = match.group(1)
            default_value = match.group(2)
            env_value = os.environ.get(var_name)
            if env_value is not None:
                return env_value
            elif default_value is not None:
                return default_value
            else:
                console.print(
                    f"[yellow]Warning: Environment variable ${{{var_name}}} is not set[/yellow]"
                )
                return match.group(0)  # Return original placeholder if not found

        return ENV_VAR_PATTERN.sub(replace_env_var, value)

    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]

    return value


def expand_remote_nodes_auth(config: dict[str, Any]) -> dict[str, Any]:
    """
    Expand environment variables in remote_nodes auth configuration.

    This function specifically targets the auth fields in remote_nodes
    where sensitive credentials are likely to be stored as env vars.

    Args:
        config: The workflow configuration dictionary

    Returns:
        The configuration with auth fields expanded
    """
    if "remote_nodes" not in config:
        return config

    remote_nodes = config.get("remote_nodes", {})
    if not isinstance(remote_nodes, dict):
        return config

    expanded_remote_nodes = {}
    for node_name, node_config in remote_nodes.items():
        if not isinstance(node_config, dict):
            expanded_remote_nodes[node_name] = node_config
            continue

        expanded_node = dict(node_config)

        # Expand auth fields
        if "auth" in expanded_node and isinstance(expanded_node["auth"], dict):
            expanded_node["auth"] = expand_env_vars(expanded_node["auth"])

        # Also expand url in case it contains env vars
        if "url" in expanded_node:
            expanded_node["url"] = expand_env_vars(expanded_node["url"])

        expanded_remote_nodes[node_name] = expanded_node

    config["remote_nodes"] = expanded_remote_nodes
    return config


def load_workflow_config(
    config_path: str, validate_only: bool = False
) -> dict[str, Any]:
    """Load workflow configuration from YAML file.

    Supports:
    - Traditional local/docker/binary nodes via `nodes:` key
    - Remote nodes via `remote_nodes:` key
    - Mixed configurations with both local and remote nodes
    - ${ENV_VAR} expansion in remote_nodes auth fields

    Args:
        config_path: Path to the workflow YAML file
        validate_only: If True, skip strict validation

    Returns:
        The parsed and processed workflow configuration

    Raises:
        FileNotFoundError: If the config file doesn't exist
        ValueError: If the YAML is invalid or required fields are missing
    """
    try:
        with open(config_path) as file:
            config = yaml.safe_load(file)

        # Handle empty YAML files (yaml.safe_load returns None for empty files)
        if config is None:
            config = {}

        # Expand environment variables in remote_nodes auth
        config = expand_remote_nodes_auth(config)

        # Skip basic validation if this is just for validation purposes
        if not validate_only:
            # Validate required fields
            # A workflow must have a name and at least one of: nodes or remote_nodes
            if "name" not in config:
                raise ValueError("Missing required field: name")

            has_local_nodes = "nodes" in config and config["nodes"]
            has_remote_nodes = "remote_nodes" in config and config["remote_nodes"]

            if not has_local_nodes and not has_remote_nodes:
                raise ValueError(
                    "Missing required field: either 'nodes' or 'remote_nodes' must be specified"
                )

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
