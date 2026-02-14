"""
Configuration management for bootstrap workflows.
"""

import os
import re
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from merobox.commands.utils import console

# Pattern to match ${ENV_VAR} or ${ENV_VAR:-default} syntax
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Valid step types for workflow validation
VALID_STEP_TYPES = [
    "install_application",
    "create_context",
    "create_identity",
    "invite_identity",
    "join_context",
    "invite_open",
    "join_open",
    "call",
    "wait",
    "wait_for_sync",
    "repeat",
    "parallel",
    "script",
    "assert",
    "json_assert",
    "get_proposal",
    "list_proposals",
    "get_proposal_approvers",
    "upload_blob",
    "create_mesh",
    "fuzzy_test",
]


# =============================================================================
# Pydantic Models for Workflow Schema Validation
# =============================================================================


class NodesConfig(BaseModel):
    """Configuration for local Docker/binary nodes."""

    model_config = ConfigDict(extra="allow")

    count: Optional[int] = Field(None, ge=1, description="Number of nodes to create")
    prefix: Optional[str] = Field(
        "calimero-node", description="Prefix for node names"
    )
    chain_id: Optional[str] = Field(None, description="Chain ID for nodes")
    image: Optional[str] = Field(None, description="Docker image for nodes")
    base_port: Optional[int] = Field(
        None, ge=1, le=65535, description="Base port for nodes"
    )
    base_rpc_port: Optional[int] = Field(
        None, ge=1, le=65535, description="Base RPC port for nodes"
    )
    config_path: Optional[str] = Field(
        None, description="Path to node configuration file"
    )
    use_image_entrypoint: Optional[bool] = Field(
        False, description="Use Docker image entrypoint"
    )


class RemoteNodeAuth(BaseModel):
    """Authentication configuration for remote nodes."""

    model_config = ConfigDict(extra="forbid")

    method: Optional[str] = Field(
        "none", description="Authentication method (none, api_key, password)"
    )
    username: Optional[str] = Field(None, description="Username for authentication")
    password: Optional[str] = Field(None, description="Password for authentication")
    api_key: Optional[str] = Field(None, description="API key for authentication")
    key: Optional[str] = Field(None, description="Alternative field for API key")


class RemoteNodeConfig(BaseModel):
    """Configuration for a single remote node."""

    model_config = ConfigDict(extra="allow")

    url: str = Field(..., description="URL of the remote node")
    auth: Optional[RemoteNodeAuth] = Field(
        None, description="Authentication configuration"
    )
    description: Optional[str] = Field(None, description="Description of the node")


class StepOutputs(BaseModel):
    """Output variable mappings for workflow steps."""

    model_config = ConfigDict(extra="allow")


class BaseStepConfig(BaseModel):
    """Base configuration for all workflow steps."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Name of the step")
    type: str = Field(..., description="Type of the step")
    outputs: Optional[dict[str, Any]] = Field(
        None, description="Output variable mappings"
    )

    @model_validator(mode="after")
    def validate_step_type(self) -> "BaseStepConfig":
        """Validate that the step type is valid."""
        if self.type not in VALID_STEP_TYPES:
            raise ValueError(
                f"Invalid step type '{self.type}'. "
                f"Valid types are: {', '.join(VALID_STEP_TYPES)}"
            )
        return self


class InstallApplicationStep(BaseStepConfig):
    """Configuration for install_application step."""

    type: Literal["install_application"] = "install_application"
    node: str = Field(..., description="Target node for installation")
    path: str = Field(..., description="Path to the WASM file")
    dev: Optional[bool] = Field(False, description="Install in dev mode")


class CreateContextStep(BaseStepConfig):
    """Configuration for create_context step."""

    type: Literal["create_context"] = "create_context"
    node: str = Field(..., description="Target node")
    application_id: str = Field(..., description="Application ID to use")
    protocol: Optional[str] = Field(None, description="Protocol to use (e.g., near)")


class CreateIdentityStep(BaseStepConfig):
    """Configuration for create_identity step."""

    type: Literal["create_identity"] = "create_identity"
    node: str = Field(..., description="Target node")


class InviteIdentityStep(BaseStepConfig):
    """Configuration for invite_identity step."""

    type: Literal["invite_identity"] = "invite_identity"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    grantee_id: str = Field(..., description="Grantee public key")
    granter_id: str = Field(..., description="Granter public key")
    capability: Optional[str] = Field("member", description="Capability to grant")


class JoinContextStep(BaseStepConfig):
    """Configuration for join_context step."""

    type: Literal["join_context"] = "join_context"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    invitee_id: str = Field(..., description="Invitee public key")
    invitation: str = Field(..., description="Invitation data")


class InviteOpenStep(BaseStepConfig):
    """Configuration for invite_open step."""

    type: Literal["invite_open"] = "invite_open"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    invitee_id: str = Field(..., description="Invitee public key")


class JoinOpenStep(BaseStepConfig):
    """Configuration for join_open step."""

    type: Literal["join_open"] = "join_open"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    public_key: Optional[str] = Field(None, description="Public key to use")


class CallStep(BaseStepConfig):
    """Configuration for call step."""

    type: Literal["call"] = "call"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    method: str = Field(..., description="Method to call")
    args: Optional[dict[str, Any]] = Field(None, description="Arguments for the call")
    executor_public_key: Optional[str] = Field(
        None, description="Public key of executor"
    )


class WaitStep(BaseStepConfig):
    """Configuration for wait step."""

    type: Literal["wait"] = "wait"
    seconds: int = Field(..., ge=0, description="Seconds to wait")
    message: Optional[str] = Field(None, description="Message to display while waiting")


class WaitForSyncStep(BaseStepConfig):
    """Configuration for wait_for_sync step."""

    type: Literal["wait_for_sync"] = "wait_for_sync"
    context_id: str = Field(..., description="Context ID to sync")
    nodes: list[str] = Field(..., description="Nodes to wait for sync")
    timeout: Optional[int] = Field(60, ge=1, description="Timeout in seconds")
    check_interval: Optional[int] = Field(2, ge=1, description="Check interval in seconds")
    trigger_sync: Optional[bool] = Field(False, description="Trigger sync before waiting")


class RepeatStep(BaseStepConfig):
    """Configuration for repeat step."""

    type: Literal["repeat"] = "repeat"
    count: int = Field(..., ge=1, description="Number of iterations")
    steps: list[dict[str, Any]] = Field(..., description="Steps to repeat")


class ParallelGroupConfig(BaseModel):
    """Configuration for a parallel execution group."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Name of the group")
    steps: list[dict[str, Any]] = Field(..., description="Steps in this group")


class ParallelStep(BaseStepConfig):
    """Configuration for parallel step."""

    type: Literal["parallel"] = "parallel"
    groups: list[ParallelGroupConfig] = Field(
        ..., description="Groups of steps to run in parallel"
    )


class ScriptStep(BaseStepConfig):
    """Configuration for script step."""

    type: Literal["script"] = "script"
    script: str = Field(..., description="Path to the script")
    target: Optional[str] = Field(
        "nodes", description="Target: 'image' or 'nodes'"
    )
    description: Optional[str] = Field(None, description="Description of the script")


class AssertStep(BaseStepConfig):
    """Configuration for assert step."""

    type: Literal["assert"] = "assert"
    condition: str = Field(..., description="Condition to assert")
    message: Optional[str] = Field(None, description="Message on failure")


class JsonAssertStep(BaseStepConfig):
    """Configuration for json_assert step."""

    type: Literal["json_assert"] = "json_assert"
    actual: str = Field(..., description="Actual value or variable")
    expected: Any = Field(..., description="Expected value")
    message: Optional[str] = Field(None, description="Message on failure")


class GetProposalStep(BaseStepConfig):
    """Configuration for get_proposal step."""

    type: Literal["get_proposal"] = "get_proposal"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    proposal_id: str = Field(..., description="Proposal ID")


class ListProposalsStep(BaseStepConfig):
    """Configuration for list_proposals step."""

    type: Literal["list_proposals"] = "list_proposals"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")


class GetProposalApproversStep(BaseStepConfig):
    """Configuration for get_proposal_approvers step."""

    type: Literal["get_proposal_approvers"] = "get_proposal_approvers"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    proposal_id: str = Field(..., description="Proposal ID")


class UploadBlobStep(BaseStepConfig):
    """Configuration for upload_blob step."""

    type: Literal["upload_blob"] = "upload_blob"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    path: str = Field(..., description="Path to the blob file")
    content_type: Optional[str] = Field(
        None, description="Content type of the blob"
    )


class CreateMeshStep(BaseStepConfig):
    """Configuration for create_mesh step."""

    type: Literal["create_mesh"] = "create_mesh"
    nodes: list[str] = Field(..., description="List of nodes to include in mesh")
    application_id: str = Field(..., description="Application ID")
    protocol: Optional[str] = Field(None, description="Protocol to use")


class FuzzyTestStep(BaseStepConfig):
    """Configuration for fuzzy_test step."""

    type: Literal["fuzzy_test"] = "fuzzy_test"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID")
    iterations: int = Field(..., ge=1, description="Number of fuzzy test iterations")
    method: str = Field(..., description="Method to fuzz")
    args: Optional[dict[str, Any]] = Field(None, description="Base arguments")


# Union type for all step types
StepConfig = Union[
    InstallApplicationStep,
    CreateContextStep,
    CreateIdentityStep,
    InviteIdentityStep,
    JoinContextStep,
    InviteOpenStep,
    JoinOpenStep,
    CallStep,
    WaitStep,
    WaitForSyncStep,
    RepeatStep,
    ParallelStep,
    ScriptStep,
    AssertStep,
    JsonAssertStep,
    GetProposalStep,
    ListProposalsStep,
    GetProposalApproversStep,
    UploadBlobStep,
    CreateMeshStep,
    FuzzyTestStep,
    BaseStepConfig,  # Fallback for any step type
]


class WorkflowConfig(BaseModel):
    """Complete workflow configuration schema."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Name of the workflow")
    description: Optional[str] = Field(None, description="Description of the workflow")

    # Node configurations (at least one of nodes or remote_nodes must be specified)
    nodes: Optional[Union[NodesConfig, dict[str, Any]]] = Field(
        None, description="Local node configuration"
    )
    remote_nodes: Optional[dict[str, RemoteNodeConfig]] = Field(
        None, description="Remote node configurations"
    )

    # Workflow steps
    steps: Optional[list[dict[str, Any]]] = Field(
        None, description="List of workflow steps"
    )

    # Lifecycle options
    nuke_on_start: Optional[bool] = Field(
        False, description="Nuke all data before starting"
    )
    nuke_on_end: Optional[bool] = Field(
        False, description="Nuke all data after workflow"
    )
    stop_all_nodes: Optional[bool] = Field(
        False, description="Stop all nodes at the end"
    )
    restart: Optional[bool] = Field(
        False, description="Restart nodes at the beginning"
    )

    # Timing options
    wait_timeout: Optional[int] = Field(
        60, ge=1, description="Timeout for waiting operations"
    )

    # Docker options
    force_pull_image: Optional[bool] = Field(
        False, description="Force pull Docker images"
    )

    # Auth service options
    auth_service: Optional[bool] = Field(
        False, description="Enable authentication service"
    )
    auth_image: Optional[str] = Field(
        None, description="Custom Docker image for auth service"
    )
    auth_use_cached: Optional[bool] = Field(
        False, description="Use cached auth tokens"
    )
    webui_use_cached: Optional[bool] = Field(
        False, description="Use cached WebUI"
    )
    auth_mode: Optional[str] = Field(
        None, description="Auth mode (e.g., embedded)"
    )

    # Logging options
    log_level: Optional[str] = Field(
        "debug", description="Log level for nodes"
    )
    rust_backtrace: Optional[str] = Field(
        "0", description="RUST_BACKTRACE setting"
    )

    # E2E and testing options
    e2e_mode: Optional[bool] = Field(
        False, description="Enable E2E testing mode"
    )
    bootstrap_nodes: Optional[list[str]] = Field(
        None, description="Bootstrap nodes to connect to"
    )

    # NEAR options
    near_devnet: Optional[bool] = Field(
        None, description="Enable NEAR devnet"
    )
    contracts_dir: Optional[str] = Field(
        None, description="Directory containing NEAR contracts"
    )


def validate_workflow_step(step: dict[str, Any], step_index: int) -> list[str]:
    """
    Validate a single workflow step and return a list of validation errors.

    Args:
        step: The step configuration dictionary
        step_index: The index of the step (for error messages)

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    step_name = step.get("name", f"Step {step_index + 1}")
    step_type = step.get("type")

    if not step_type:
        errors.append(f"Step '{step_name}' (index {step_index}): Missing required field 'type'")
        return errors

    if step_type not in VALID_STEP_TYPES:
        errors.append(
            f"Step '{step_name}' (index {step_index}): Invalid step type '{step_type}'. "
            f"Valid types are: {', '.join(VALID_STEP_TYPES)}"
        )
        return errors

    # Type-specific validation
    step_type_models = {
        "install_application": InstallApplicationStep,
        "create_context": CreateContextStep,
        "create_identity": CreateIdentityStep,
        "invite_identity": InviteIdentityStep,
        "join_context": JoinContextStep,
        "invite_open": InviteOpenStep,
        "join_open": JoinOpenStep,
        "call": CallStep,
        "wait": WaitStep,
        "wait_for_sync": WaitForSyncStep,
        "repeat": RepeatStep,
        "parallel": ParallelStep,
        "script": ScriptStep,
        "assert": AssertStep,
        "json_assert": JsonAssertStep,
        "get_proposal": GetProposalStep,
        "list_proposals": ListProposalsStep,
        "get_proposal_approvers": GetProposalApproversStep,
        "upload_blob": UploadBlobStep,
        "create_mesh": CreateMeshStep,
        "fuzzy_test": FuzzyTestStep,
    }

    model_class = step_type_models.get(step_type)
    if model_class:
        try:
            model_class.model_validate(step)
        except Exception as e:
            error_msg = str(e)
            # Extract the most relevant part of the Pydantic error
            if "validation error" in error_msg.lower():
                # Parse Pydantic validation errors for cleaner output
                lines = error_msg.split("\n")
                for line in lines[1:]:  # Skip the first line which is generic
                    line = line.strip()
                    if line and not line.startswith("For further"):
                        errors.append(
                            f"Step '{step_name}' (index {step_index}): {line}"
                        )
            else:
                errors.append(f"Step '{step_name}' (index {step_index}): {error_msg}")

    # Recursively validate nested steps (for repeat and parallel)
    if step_type == "repeat":
        nested_steps = step.get("steps", [])
        for i, nested_step in enumerate(nested_steps):
            nested_errors = validate_workflow_step(
                nested_step, i
            )
            for err in nested_errors:
                errors.append(f"Step '{step_name}' (index {step_index}) -> {err}")

    elif step_type == "parallel":
        groups = step.get("groups", [])
        for g_idx, group in enumerate(groups):
            group_name = group.get("name", f"Group {g_idx + 1}")
            nested_steps = group.get("steps", [])
            for i, nested_step in enumerate(nested_steps):
                nested_errors = validate_workflow_step(nested_step, i)
                for err in nested_errors:
                    errors.append(
                        f"Step '{step_name}' (index {step_index}) -> {group_name} -> {err}"
                    )

    return errors


def validate_workflow_config(config: dict[str, Any]) -> list[str]:
    """
    Validate workflow configuration and return a list of validation errors.

    This function validates the entire workflow configuration including:
    - Required top-level fields (name, nodes/remote_nodes)
    - Node configuration structure
    - All workflow steps and their required fields

    Args:
        config: The workflow configuration dictionary

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Validate top-level structure
    try:
        WorkflowConfig.model_validate(config)
    except Exception as e:
        error_msg = str(e)
        if "validation error" in error_msg.lower():
            lines = error_msg.split("\n")
            for line in lines[1:]:
                line = line.strip()
                if line and not line.startswith("For further"):
                    errors.append(f"Workflow config: {line}")
        else:
            errors.append(f"Workflow config: {error_msg}")

    # Check for required nodes configuration
    if not config.get("nodes") and not config.get("remote_nodes"):
        errors.append(
            "Workflow config: At least one of 'nodes' or 'remote_nodes' must be specified"
        )

    # Validate each step
    steps = config.get("steps", [])
    for i, step in enumerate(steps):
        step_errors = validate_workflow_step(step, i)
        errors.extend(step_errors)

    return errors


def format_validation_errors(errors: list[str]) -> str:
    """
    Format validation errors into a user-friendly message.

    Args:
        errors: List of validation error messages

    Returns:
        Formatted error message string
    """
    if not errors:
        return ""

    header = "Workflow configuration validation failed:\n"
    error_list = "\n".join(f"  - {err}" for err in errors)
    footer = "\n\nPlease check your workflow YAML file and fix the issues above."

    return f"{header}{error_list}{footer}"


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
    config_path: str, validate_only: bool = False, skip_schema_validation: bool = False
) -> dict[str, Any]:
    """Load workflow configuration from YAML file.

    Supports:
    - Traditional local/docker/binary nodes via `nodes:` key
    - Remote nodes via `remote_nodes:` key
    - Mixed configurations with both local and remote nodes
    - ${ENV_VAR} expansion in remote_nodes auth fields
    - Full schema validation with helpful error messages

    Args:
        config_path: Path to the workflow YAML file
        validate_only: If True, skip strict validation (legacy behavior)
        skip_schema_validation: If True, skip Pydantic schema validation

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

        # Perform schema validation unless explicitly skipped
        if not skip_schema_validation:
            validation_errors = validate_workflow_config(config)
            if validation_errors:
                error_message = format_validation_errors(validation_errors)
                # Print detailed errors to console for visibility
                console.print(f"[red]{error_message}[/red]")
                raise ValueError(error_message)

        return config

    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Workflow configuration file not found: {config_path}"
        ) from e
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML format: {str(e)}") from e
    except ValueError:
        # Re-raise ValueError as-is (includes our validation errors)
        raise
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
