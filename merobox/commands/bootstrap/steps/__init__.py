"""
Steps module - Individual step implementations for workflow execution.
"""

from merobox.commands.bootstrap.steps.assert_log import (
    AssertLogAbsentStep,
    AssertLogPresentStep,
)
from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.blob import UploadBlobStep
from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.delete_blob import (
    DeleteBlobOnDiskStep,
    DeleteBlobStep,
)
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.fault import InjectNetworkFaultStep
from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep
from merobox.commands.bootstrap.steps.get_application import GetApplicationStep
from merobox.commands.bootstrap.steps.group_create import (
    CreateGroupStep,
    CreateNamespaceStep,
)
from merobox.commands.bootstrap.steps.group_governance import (
    DetachContextFromGroupStep,
    SyncGroupStep,
    UpdateGroupSettingsStep,
)
from merobox.commands.bootstrap.steps.group_invite import (
    CreateGroupInvitationStep,
    CreateNamespaceInvitationStep,
)
from merobox.commands.bootstrap.steps.group_join import JoinGroupStep, JoinNamespaceStep
from merobox.commands.bootstrap.steps.group_management import (
    DeleteContextStep,
    DeleteGroupStep,
    DeleteNamespaceStep,
    GetGroupInfoStep,
    GetMemberCapabilitiesStep,
    LeaveContextStep,
    LeaveGroupStep,
    LeaveNamespaceStep,
    ListGroupContextsStep,
    ListGroupMembersStep,
    RemoveGroupMembersStep,
    SetDefaultCapabilitiesStep,
    SetDefaultVisibilityStep,
    SetMemberAutoFollowStep,
    SetMemberCapabilitiesStep,
    SetSubgroupVisibilityStep,
    UninstallApplicationStep,
    UpdateMemberRoleStep,
)
from merobox.commands.bootstrap.steps.group_metadata import (
    GetContextMetadataStep,
    GetGroupMetadataStep,
    GetMemberMetadataStep,
    SetContextMetadataStep,
    SetGroupMetadataStep,
    SetMemberMetadataStep,
)
from merobox.commands.bootstrap.steps.group_upgrade import (
    AbortMigrationStep,
    AssertCascadeCompleteStep,
    AssertMigrationCompleteStep,
    CascadeNamespaceApplicationStep,
    GetCascadeStatusStep,
    GetGroupUpgradeStatusStep,
    GetMigrationStatusStep,
    ListApplicationVersionsStep,
    RegisterGroupSigningKeyStep,
    ResyncContextStep,
    RetryGroupUpgradeStep,
    UpgradeGroupStep,
)
from merobox.commands.bootstrap.steps.identity import CreateIdentityStep
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.invite_open import InviteOpenStep
from merobox.commands.bootstrap.steps.join import (
    JoinNamespaceStep as JoinInvitationStep,
)
from merobox.commands.bootstrap.steps.join import (
    JoinNamespaceStep as JoinNamespaceAliasStep,
)
from merobox.commands.bootstrap.steps.join_context import JoinContextStep
from merobox.commands.bootstrap.steps.join_subgroup_inheritance import (
    JoinSubgroupInheritanceStep,
)
from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep
from merobox.commands.bootstrap.steps.mesh import CreateMeshStep
from merobox.commands.bootstrap.steps.namespace import (
    CreateGroupInNamespaceStep,
    GetNamespaceIdentityStep,
    ListNamespaceGroupsStep,
    ListNamespacesStep,
)
from merobox.commands.bootstrap.steps.network import (
    ConnectNodeStep,
    DisconnectNodeStep,
    HealPeersStep,
    PartitionPeersStep,
)
from merobox.commands.bootstrap.steps.parallel import ParallelStep
from merobox.commands.bootstrap.steps.pause import (
    PauseContainerStep,
    UnpauseContainerStep,
)
from merobox.commands.bootstrap.steps.repeat import RepeatStep
from merobox.commands.bootstrap.steps.restart import RestartContainerStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.start_node import StartNodeStep
from merobox.commands.bootstrap.steps.stop_node import StopNodeStep
from merobox.commands.bootstrap.steps.subgroup import (
    AddGroupMembersStep,
    ListSubgroupsStep,
    ReparentGroupStep,
)
from merobox.commands.bootstrap.steps.tee import (
    AssertNotMemberStep,
    AssertTeeMemberStep,
    SetTeeAdmissionPolicyStep,
    TeeFleetJoinStep,
)
from merobox.commands.bootstrap.steps.wait import WaitStep
from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep

# Backward-compat aliases
InviteIdentityStep = InviteOpenStep
JoinOpenStep = JoinInvitationStep

__all__ = [
    "BaseStep",
    "InstallApplicationStep",
    "CreateContextStep",
    "CreateNamespaceStep",
    "CreateNamespaceInvitationStep",
    "JoinNamespaceStep",
    "CreateGroupStep",
    "CreateGroupInvitationStep",
    "JoinGroupStep",
    "ListNamespacesStep",
    "GetNamespaceIdentityStep",
    "CreateGroupInNamespaceStep",
    "ListNamespaceGroupsStep",
    "ReparentGroupStep",
    "AddGroupMembersStep",
    "ListSubgroupsStep",
    "CreateIdentityStep",
    "InviteOpenStep",
    "InviteIdentityStep",
    "JoinContextStep",
    "JoinSubgroupInheritanceStep",
    "JoinInvitationStep",
    "JoinNamespaceAliasStep",
    "JoinOpenStep",
    "ExecuteStep",
    "WaitStep",
    "WaitForSyncStep",
    "RepeatStep",
    "ParallelStep",
    "ScriptStep",
    "PauseContainerStep",
    "UnpauseContainerStep",
    "RestartContainerStep",
    "DisconnectNodeStep",
    "ConnectNodeStep",
    "PartitionPeersStep",
    "HealPeersStep",
    "InjectNetworkFaultStep",
    "AssertStep",
    "AssertLogAbsentStep",
    "AssertLogPresentStep",
    "JsonAssertStep",
    "UploadBlobStep",
    "DeleteBlobOnDiskStep",
    "DeleteBlobStep",
    "GetApplicationStep",
    "CreateMeshStep",
    "FuzzyTestStep",
    "RemoveGroupMembersStep",
    "ListGroupMembersStep",
    "UpdateMemberRoleStep",
    "SetMemberCapabilitiesStep",
    "SetMemberAutoFollowStep",
    "GetMemberCapabilitiesStep",
    "SetDefaultCapabilitiesStep",
    "SetDefaultVisibilityStep",
    "SetSubgroupVisibilityStep",
    "GetGroupInfoStep",
    "ListGroupContextsStep",
    "DeleteGroupStep",
    "DeleteNamespaceStep",
    "DeleteContextStep",
    "LeaveContextStep",
    "LeaveGroupStep",
    "LeaveNamespaceStep",
    "UninstallApplicationStep",
    "SetGroupMetadataStep",
    "GetGroupMetadataStep",
    "SetMemberMetadataStep",
    "GetMemberMetadataStep",
    "SetContextMetadataStep",
    "GetContextMetadataStep",
    "UpdateGroupSettingsStep",
    "DetachContextFromGroupStep",
    "SyncGroupStep",
    "RegisterGroupSigningKeyStep",
    "UpgradeGroupStep",
    "AbortMigrationStep",
    "CascadeNamespaceApplicationStep",
    "GetCascadeStatusStep",
    "AssertCascadeCompleteStep",
    "GetMigrationStatusStep",
    "AssertMigrationCompleteStep",
    "ResyncContextStep",
    "ListApplicationVersionsStep",
    "GetGroupUpgradeStatusStep",
    "RetryGroupUpgradeStep",
    "StopNodeStep",
    "StartNodeStep",
    "SetTeeAdmissionPolicyStep",
    "TeeFleetJoinStep",
    "AssertTeeMemberStep",
    "AssertNotMemberStep",
]
