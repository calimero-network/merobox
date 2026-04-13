"""
Steps module - Individual step implementations for workflow execution.
"""

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.blob import UploadBlobStep
from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep
from merobox.commands.bootstrap.steps.group_create import (
    CreateGroupStep,
    CreateNamespaceStep,
)
from merobox.commands.bootstrap.steps.group_invite import (
    CreateGroupInvitationStep,
    CreateNamespaceInvitationStep,
)
from merobox.commands.bootstrap.steps.group_join import JoinGroupStep, JoinNamespaceStep
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
from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep
from merobox.commands.bootstrap.steps.mesh import CreateMeshStep
from merobox.commands.bootstrap.steps.namespace import (
    CreateGroupInNamespaceStep,
    GetNamespaceIdentityStep,
    ListNamespaceGroupsStep,
    ListNamespacesStep,
)
from merobox.commands.bootstrap.steps.parallel import ParallelStep
from merobox.commands.bootstrap.steps.repeat import RepeatStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.subgroup import (
    AddGroupMembersStep,
    ListSubgroupsStep,
    NestGroupStep,
    UnnestGroupStep,
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
    "NestGroupStep",
    "UnnestGroupStep",
    "AddGroupMembersStep",
    "ListSubgroupsStep",
    "CreateIdentityStep",
    "InviteOpenStep",
    "InviteIdentityStep",
    "JoinContextStep",
    "JoinInvitationStep",
    "JoinNamespaceAliasStep",
    "JoinOpenStep",
    "ExecuteStep",
    "WaitStep",
    "WaitForSyncStep",
    "RepeatStep",
    "ParallelStep",
    "ScriptStep",
    "AssertStep",
    "JsonAssertStep",
    "UploadBlobStep",
    "CreateMeshStep",
    "FuzzyTestStep",
]
