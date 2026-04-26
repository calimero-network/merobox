"""
Workflow configuration validator.

This module provides comprehensive validation for workflow configurations
without requiring full workflow execution.
"""

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep
from merobox.commands.bootstrap.steps.group_alias import (
    SetGroupAliasStep,
    SetMemberAliasStep,
)
from merobox.commands.bootstrap.steps.group_create import CreateNamespaceStep
from merobox.commands.bootstrap.steps.group_governance import (
    DetachContextFromGroupStep,
    SyncGroupStep,
    UpdateGroupSettingsStep,
)
from merobox.commands.bootstrap.steps.group_invite import CreateNamespaceInvitationStep
from merobox.commands.bootstrap.steps.group_join import JoinNamespaceStep
from merobox.commands.bootstrap.steps.group_management import (
    DeleteContextStep,
    DeleteGroupStep,
    DeleteNamespaceStep,
    GetGroupInfoStep,
    GetMemberCapabilitiesStep,
    ListGroupContextsStep,
    ListGroupMembersStep,
    RemoveGroupMembersStep,
    SetDefaultCapabilitiesStep,
    SetDefaultVisibilityStep,
    SetSubgroupVisibilityStep,
    SetMemberCapabilitiesStep,
    UninstallApplicationStep,
    UpdateMemberRoleStep,
)
from merobox.commands.bootstrap.steps.group_upgrade import (
    GetGroupUpgradeStatusStep,
    RegisterGroupSigningKeyStep,
    RetryGroupUpgradeStep,
    UpgradeGroupStep,
)
from merobox.commands.bootstrap.steps.identity import CreateIdentityStep
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.invite_open import InviteOpenStep
from merobox.commands.bootstrap.steps.join import (
    JoinNamespaceStep as JoinInvitationStep,
)
from merobox.commands.bootstrap.steps.join_context import JoinContextStep
from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep
from merobox.commands.bootstrap.steps.mesh import CreateMeshStep
from merobox.commands.bootstrap.steps.namespace import (
    CreateGroupInNamespaceStep,
    GetNamespaceIdentityStep,
    ListNamespaceGroupsStep,
    ListNamespacesStep,
)
from merobox.commands.bootstrap.steps.parallel import ParallelStep
from merobox.commands.bootstrap.steps.proposals import (
    GetProposalApproversStep,
    GetProposalStep,
    ListProposalsStep,
)
from merobox.commands.bootstrap.steps.repeat import RepeatStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.subgroup import (
    AddGroupMembersStep,
    ListSubgroupsStep,
    ReparentGroupStep,
)
from merobox.commands.bootstrap.steps.wait import WaitStep
from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep
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
                # Validate config_path and count compatibility
                if "config_path" in nodes:
                    errors.append(
                        "config_path is not supported with 'count' mode. "
                        "Please define nodes individually or remove config_path."
                    )
            elif not has_individual_nodes:
                # Neither count mode nor individual nodes defined
                errors.append(
                    "Nodes configuration must either use 'count' mode (with prefix, image) "
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
        elif step_type == "create_mesh":
            step_class = CreateMeshStep
        elif step_type in ("invite", "invite_identity", "invite_open"):
            step_class = InviteOpenStep
        elif step_type in ("join", "join_open"):
            step_class = JoinInvitationStep
        elif step_type == "call":
            step_class = ExecuteStep
        elif step_type == "wait":
            step_class = WaitStep
        elif step_type == "wait_for_sync":
            step_class = WaitForSyncStep
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
        elif step_type in ("create_namespace", "create_group"):
            step_class = CreateNamespaceStep
        elif step_type in ("create_namespace_invitation", "create_group_invitation"):
            step_class = CreateNamespaceInvitationStep
        elif step_type in ("join_namespace", "join_group"):
            step_class = JoinNamespaceStep
        elif step_type == "join_context":
            step_class = JoinContextStep
        elif step_type == "list_namespaces":
            step_class = ListNamespacesStep
        elif step_type == "get_namespace_identity":
            step_class = GetNamespaceIdentityStep
        elif step_type == "create_group_in_namespace":
            step_class = CreateGroupInNamespaceStep
        elif step_type == "list_namespace_groups":
            step_class = ListNamespaceGroupsStep
        elif step_type == "reparent_group":
            step_class = ReparentGroupStep
        elif step_type == "list_subgroups":
            step_class = ListSubgroupsStep
        elif step_type == "add_group_members":
            step_class = AddGroupMembersStep
        elif step_type == "remove_group_members":
            step_class = RemoveGroupMembersStep
        elif step_type == "list_group_members":
            step_class = ListGroupMembersStep
        elif step_type == "list_group_contexts":
            step_class = ListGroupContextsStep
        elif step_type == "update_member_role":
            step_class = UpdateMemberRoleStep
        elif step_type == "set_member_capabilities":
            step_class = SetMemberCapabilitiesStep
        elif step_type == "get_member_capabilities":
            step_class = GetMemberCapabilitiesStep
        elif step_type == "set_default_capabilities":
            step_class = SetDefaultCapabilitiesStep
        elif step_type == "set_subgroup_visibility":
            step_class = SetSubgroupVisibilityStep
        elif step_type == "set_default_visibility":
            # Deprecated alias retained for backward compat with workflows
            # written before calimero-network/core#2256.
            step_class = SetDefaultVisibilityStep
        elif step_type == "get_group_info":
            step_class = GetGroupInfoStep
        elif step_type == "delete_group":
            step_class = DeleteGroupStep
        elif step_type == "delete_namespace":
            step_class = DeleteNamespaceStep
        elif step_type == "delete_context":
            step_class = DeleteContextStep
        elif step_type == "uninstall_application":
            step_class = UninstallApplicationStep
        elif step_type == "set_group_alias":
            step_class = SetGroupAliasStep
        elif step_type == "set_member_alias":
            step_class = SetMemberAliasStep
        elif step_type == "update_group_settings":
            step_class = UpdateGroupSettingsStep
        elif step_type == "detach_context_from_group":
            step_class = DetachContextFromGroupStep
        elif step_type == "sync_group":
            step_class = SyncGroupStep
        elif step_type == "register_group_signing_key":
            step_class = RegisterGroupSigningKeyStep
        elif step_type == "upgrade_group":
            step_class = UpgradeGroupStep
        elif step_type == "get_group_upgrade_status":
            step_class = GetGroupUpgradeStatusStep
        elif step_type == "retry_group_upgrade":
            step_class = RetryGroupUpgradeStep
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
