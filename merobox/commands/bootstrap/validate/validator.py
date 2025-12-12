"""
Workflow configuration validator.

This module provides comprehensive validation for workflow configurations
without requiring full workflow execution.
"""

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep
from merobox.commands.bootstrap.steps.identity import (
    CreateIdentityStep,
    InviteIdentityStep,
)
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.invite_open import InviteOpenStep
from merobox.commands.bootstrap.steps.join import JoinContextStep
from merobox.commands.bootstrap.steps.join_open import JoinOpenStep
from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep
from merobox.commands.bootstrap.steps.parallel import ParallelStep
from merobox.commands.bootstrap.steps.proposals import (
    GetProposalApproversStep,
    GetProposalStep,
    ListProposalsStep,
)
from merobox.commands.bootstrap.steps.repeat import RepeatStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.wait import WaitStep
from merobox.commands.constants import RESERVED_NODE_CONFIG_KEYS


def validate_workflow_config(config: dict, verbose: bool = False) -> dict:
    """
    Validate a workflow configuration without executing it.

    Args:
        config: The workflow configuration dictionary
        verbose: Whether to show detailed validation information

    Returns:
        Dictionary with 'valid' boolean and 'errors' list
    """
    errors = []

    # Check required top-level fields
    required_fields = ["name", "nodes", "steps"]
    for field in required_fields:
        if field not in config:
            errors.append(f"Missing required field: {field}")

    # Validate nodes configuration
    if "nodes" in config:
        nodes = config["nodes"]
        if not isinstance(nodes, dict):
            errors.append("'nodes' must be a dictionary")
        else:
            # Validate count mode vs individual node mode
            has_count = "count" in nodes
            has_individual_nodes = any(
                key not in RESERVED_NODE_CONFIG_KEYS for key in nodes.keys()
            )

            if has_count:
                # Count mode: require chain_id, image, prefix
                required_count_fields = ["chain_id", "image", "prefix"]
                for field in required_count_fields:
                    if field not in nodes:
                        errors.append(
                            f"Missing required node field for 'count' mode: {field}"
                        )

                # Validate config_path and count compatibility
                if "config_path" in nodes:
                    errors.append(
                        "config_path is not supported with 'count' mode. "
                        "Please define nodes individually or remove config_path."
                    )
            elif not has_individual_nodes:
                # Neither count mode nor individual nodes defined
                errors.append(
                    "Nodes configuration must either use 'count' mode (with chain_id, image, prefix) "
                    "or define individual nodes."
                )

    # Validate steps configuration
    if "steps" in config:
        steps = config["steps"]
        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
        elif len(steps) == 0:
            errors.append("'steps' list cannot be empty")
        else:
            # Validate each step
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"Step {i+1} must be a dictionary")
                    continue

                step_name = step.get("name", f"Step {i+1}")
                step_type = step.get("type")

                if not step_type:
                    errors.append(f"Step '{step_name}' is missing 'type' field")
                    continue

                # Validate step-specific requirements
                step_errors = validate_step_config(step, step_name, step_type)
                errors.extend(step_errors)

    return {"valid": len(errors) == 0, "errors": errors}


def validate_step_config(step: dict, step_name: str, step_type: str) -> list:
    """
    Validate a single step configuration.

    Args:
        step: The step configuration dictionary
        step_name: Name of the step for error reporting
        step_type: Type of the step

    Returns:
        List of validation errors
    """
    errors = []

    # Import step classes dynamically to avoid circular imports
    try:
        if step_type == "install_application":
            step_class = InstallApplicationStep
        elif step_type == "create_context":
            step_class = CreateContextStep
        elif step_type == "create_identity":
            step_class = CreateIdentityStep
        elif step_type == "invite_identity":
            step_class = InviteIdentityStep
        elif step_type == "join_context":
            step_class = JoinContextStep
        elif step_type == "invite_open":
            step_class = InviteOpenStep
        elif step_type == "join_open":
            step_class = JoinOpenStep
        elif step_type == "call":
            step_class = ExecuteStep
        elif step_type == "wait":
            step_class = WaitStep
        elif step_type == "repeat":
            step_class = RepeatStep
        elif step_type == "parallel":
            step_class = ParallelStep
        elif step_type == "script":
            step_class = ScriptStep
        elif step_type == "assert":
            step_class = AssertStep
        elif step_type == "json_assert":
            step_class = JsonAssertStep
        elif step_type == "get_proposal":
            step_class = GetProposalStep
        elif step_type == "list_proposals":
            step_class = ListProposalsStep
        elif step_type == "get_proposal_approvers":
            step_class = GetProposalApproversStep
        elif step_type == "fuzzy_test":
            step_class = FuzzyTestStep
        else:
            errors.append(f"Step '{step_name}' has unknown type: {step_type}")
            return errors

        # Create a temporary step instance to trigger validation
        # This will catch any validation errors without executing
        try:
            step_class(step)
        except Exception as e:
            errors.append(f"Step '{step_name}' validation failed: {str(e)}")

    except ImportError as e:
        errors.append(
            f"Step '{step_name}' validation failed: Could not import step class for type '{step_type}': {str(e)}"
        )

    return errors
