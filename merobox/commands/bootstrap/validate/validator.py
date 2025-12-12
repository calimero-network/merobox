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
from merobox.commands.bootstrap.steps.proposals import (
    GetProposalApproversStep,
    GetProposalStep,
    ListProposalsStep,
)
from merobox.commands.bootstrap.steps.repeat import RepeatStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.wait import WaitStep


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

    # Validate top-level variables if present
    if "variables" in config:
        from merobox.commands.bootstrap.config import validate_workflow_variables

        try:
            validate_workflow_variables(config["variables"])
        except ValueError as e:
            errors.append(f"Top-level variables validation failed: {str(e)}")

    # Validate nodes configuration
    if "nodes" in config:
        nodes = config["nodes"]
        if not isinstance(nodes, dict):
            errors.append("'nodes' must be a dictionary")
        else:
            required_node_fields = ["chain_id", "count", "image", "prefix"]
            for field in required_node_fields:
                if field not in nodes:
                    errors.append(f"Missing required node field: {field}")

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

                # Validate inline variables field if present
                if "variables" in step:
                    var_errors = validate_inline_variables(step["variables"], step_name)
                    errors.extend(var_errors)

                # Validate step-specific requirements
                step_errors = validate_step_config(step, step_name, step_type)
                errors.extend(step_errors)

    return {"valid": len(errors) == 0, "errors": errors}


def validate_inline_variables(variables: dict, step_name: str) -> list:
    """
    Validate inline variables field in a step.

    Args:
        variables: The variables dictionary
        step_name: Name of the step for error reporting

    Returns:
        List of validation errors
    """
    errors = []

    if not isinstance(variables, dict):
        errors.append(f"Step '{step_name}': 'variables' must be a dictionary")
        return errors

    from merobox.commands.bootstrap.config import validate_variable_name

    for var_name, var_value in variables.items():
        # Check if this is a scoped variable
        if var_name.startswith("local:") or var_name.startswith("global:"):
            # Extract actual variable name
            actual_name = var_name.split(":", 1)[1]
            if not validate_variable_name(actual_name):
                errors.append(
                    f"Step '{step_name}': Invalid scoped variable name '{actual_name}' "
                    "(must start with letter/underscore, contain only alphanumeric/underscore, "
                    "and not be a reserved keyword)"
                )
        else:
            # Regular variable name
            if not validate_variable_name(var_name):
                errors.append(
                    f"Step '{step_name}': Invalid variable name '{var_name}' "
                    "(must start with letter/underscore, contain only alphanumeric/underscore, "
                    "and not be a reserved keyword)"
                )

        # Validate variable value type (must be serializable)
        if not isinstance(var_value, (str, int, float, bool, type(None))):
            errors.append(
                f"Step '{step_name}': Variable '{var_name}' has invalid type. "
                "Variables must be strings, numbers, booleans, or null."
            )

    return errors


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
        elif step_type == "run_workflow":
            from merobox.commands.bootstrap.steps import RunWorkflowStep

            step_class = RunWorkflowStep
        elif step_type == "run_workflows":
            from merobox.commands.bootstrap.steps import RunWorkflowsStep

            step_class = RunWorkflowsStep
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
