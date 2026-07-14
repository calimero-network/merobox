"""
Configuration management for bootstrap workflows.
"""

import math
import os
import re
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from merobox.commands.utils import console

# Pattern to match ${ENV_VAR} or ${ENV_VAR:-default} syntax
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Valid step types for workflow validation (using frozenset for O(1) lookups)
VALID_STEP_TYPES = frozenset(
    {
        "install_application",
        "create_context",
        "create_namespace",
        "create_namespace_invitation",
        "join_namespace",
        # Deprecated aliases (kept for backward compatibility)
        "create_group",
        "create_group_invitation",
        "join_group",
        "invite",
        "invite_open",
        "invite_identity",
        "join",
        "join_open",
        "create_identity",
        "join_context",
        "join_subgroup_inheritance",
        "leave_context",
        "leave_group",
        "leave_namespace",
        "list_namespaces",
        "get_namespace_identity",
        "create_group_in_namespace",
        "list_namespace_groups",
        "reparent_group",
        "list_subgroups",
        "add_group_members",
        "remove_group_members",
        "list_group_members",
        "update_member_role",
        "set_member_capabilities",
        "set_member_auto_follow",
        "get_member_capabilities",
        "set_default_capabilities",
        "set_default_visibility",
        "set_subgroup_visibility",
        "get_group_info",
        "list_group_contexts",
        "delete_group",
        "delete_namespace",
        "delete_context",
        "uninstall_application",
        "set_group_metadata",
        "get_group_metadata",
        "set_member_metadata",
        "get_member_metadata",
        "set_context_metadata",
        "get_context_metadata",
        "update_group_settings",
        "detach_context_from_group",
        "sync_group",
        "register_group_signing_key",
        "upgrade_group",
        "cascade_namespace_application",
        "get_cascade_status",
        "assert_cascade_complete",
        "abort_migration",
        "get_migration_status",
        "assert_migration_complete",
        "resync_context",
        "list_application_versions",
        "get_group_upgrade_status",
        "retry_group_upgrade",
        "call",
        "wait",
        "wait_for_sync",
        "repeat",
        "parallel",
        "script",
        "pause_container",
        "unpause_container",
        "restart_container",
        "disconnect_node",
        "connect_node",
        "partition_peers",
        "heal_peers",
        "inject_network_fault",
        "assert",
        "json_assert",
        "assert_log_absent",
        "assert_log_present",
        "get_proposal",
        "list_proposals",
        "get_proposal_approvers",
        "upload_blob",
        "delete_blob_on_disk",
        "delete_blob",
        "get_application",
        "create_mesh",
        "fuzzy_test",
        "stop_node",
        "start_node",
        "set_tee_admission_policy",
        "tee_fleet_join",
        "assert_tee_member",
        "assert_not_member",
        "login",
        "refresh",
        "ws_connect",
        "ws_subscribe",
    }
)


# =============================================================================
# Pydantic Models for Workflow Schema Validation
# =============================================================================


class NodesConfig(BaseModel):
    """Configuration for local Docker/binary nodes."""

    model_config = ConfigDict(extra="allow")

    count: Optional[int] = Field(None, ge=1, description="Number of nodes to create")
    prefix: Optional[str] = Field("calimero-node", description="Prefix for node names")
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
    mdns: Optional[bool] = Field(
        None,
        description=(
            "Force discovery.mdns in node config. Set to false to exercise "
            "the rendezvous/relay code path under fault-injection workflows."
        ),
    )
    network_admin: Optional[bool] = Field(
        None,
        description=(
            "Add NET_ADMIN capability to node containers. Default true so "
            "inject_network_fault works out of the box; set false to opt out."
        ),
    )


class RemoteNodeAuth(BaseModel):
    """Authentication configuration for remote nodes."""

    model_config = ConfigDict(extra="forbid")

    method: Optional[Literal["none", "api_key", "password"]] = Field(
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
                f"Valid types are: {', '.join(sorted(VALID_STEP_TYPES))}"
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
    group_id: str = Field(..., description="Namespace/group ID for the context")
    service_name: Optional[str] = Field(
        None, description="Optional service name for context creation"
    )


class CreateIdentityStep(BaseStepConfig):
    """Configuration for create_identity step."""

    type: Literal["create_identity"] = "create_identity"
    node: str = Field(..., description="Target node")


class InviteStep(BaseStepConfig):
    """Configuration for deprecated invite aliases.

    Legacy step types 'invite', 'invite_open', and 'invite_identity' map to
    namespace invitation creation.
    """

    type: Literal["invite", "invite_open", "invite_identity"] = "invite"
    node: str = Field(..., description="Target node")
    namespace_id: Optional[str] = Field(
        None, description="Namespace ID to create invitation for"
    )
    recursive: Optional[bool] = Field(False, description="Create recursive invitation")

    @model_validator(mode="after")
    def normalize_namespace_id(self) -> "InviteStep":
        # Backward compatibility: accept group_id as alias of namespace_id.
        group_id = getattr(self, "group_id", None)
        if not self.namespace_id and group_id:
            self.namespace_id = group_id
        if not self.namespace_id:
            raise ValueError(
                "Either 'namespace_id' or deprecated alias 'group_id' is required"
            )
        return self


class JoinStep(BaseStepConfig):
    """Configuration for deprecated join aliases."""

    type: Literal["join", "join_open"] = "join"
    node: str = Field(..., description="Target node")
    namespace_id: Optional[str] = Field(None, description="Namespace ID to join")
    invitation: str = Field(..., description="Namespace invitation data")

    @model_validator(mode="after")
    def normalize_namespace_id(self) -> "JoinStep":
        # Backward compatibility: accept group_id as alias of namespace_id.
        group_id = getattr(self, "group_id", None)
        if not self.namespace_id and group_id:
            self.namespace_id = group_id
        if not self.namespace_id:
            raise ValueError(
                "Either 'namespace_id' or deprecated alias 'group_id' is required"
            )
        return self


class JoinContextStepConfig(BaseStepConfig):
    """Configuration for join_context step (join existing context via group membership)."""

    type: Literal["join_context"] = "join_context"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID to join")


class JoinSubgroupInheritanceStepConfig(BaseStepConfig):
    """Configuration for join_subgroup_inheritance step.

    Calls the `POST /admin-api/groups/:group_id/join-via-inheritance`
    endpoint from calimero-network/core#2357 so the target node
    materialises its inherited Open-subgroup membership without an
    admin-signed invitation or a prior `join_context`.
    """

    type: Literal["join_subgroup_inheritance"] = "join_subgroup_inheritance"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Open subgroup ID to join via inheritance")


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
    expected_failure: Optional[bool] = Field(
        False, description="Assert the call is rejected/fails rather than succeeds"
    )
    unauthenticated: Optional[bool] = Field(
        False,
        description=(
            "Force a no-token request (negative auth test). Pair with "
            "expected_failure: true to assert a 401."
        ),
    )


class LoginStepConfig(BaseStepConfig):
    """Configuration for login step (embedded-auth bootstrap/login)."""

    type: Literal["login"] = "login"
    node: str = Field(..., description="Target node to authenticate against")
    bootstrap_secret: Optional[str] = Field(
        None,
        description=(
            "Out-of-band secret required by core to mint the FIRST root key on "
            "a fresh node (sent as provider_data.bootstrap_secret). Defaults "
            "from the MERO_AUTH_BOOTSTRAP_SECRET environment variable; omitted "
            "when unset. Existing-user logins never need it."
        ),
    )
    username: str = Field(
        ..., description="Username (public key) for user_password auth"
    )
    password: str = Field(..., description="Password for user_password auth")
    expected_failure: Optional[bool] = Field(
        False,
        description="Assert that authentication is rejected (e.g. bad credentials)",
    )


class RefreshStepConfig(BaseStepConfig):
    """Configuration for refresh step (POST /auth/refresh)."""

    type: Literal["refresh"] = "refresh"
    node: str = Field(..., description="Node whose cached token should be refreshed")
    expected_failure: Optional[bool] = Field(
        False, description="Assert that the refresh is rejected (e.g. invalid token)"
    )


class WebSocketConnectStepConfig(BaseStepConfig):
    """Configuration for ws_connect / ws_subscribe step (WebSocket auth)."""

    type: Literal["ws_connect", "ws_subscribe"] = "ws_connect"
    node: str = Field(..., description="Target node to open a WebSocket against")
    unauthenticated: Optional[bool] = Field(
        False, description="Connect without attaching a token (negative auth test)"
    )
    expected_failure: Optional[bool] = Field(
        False,
        description="Assert the connection is rejected (use with unauthenticated)",
    )
    token: Optional[str] = Field(
        None, description="Explicit JWT to attach (overrides the cached token)"
    )
    message: Optional[str] = Field(
        None, description="Optional text frame to send once connected"
    )
    timeout: Optional[float] = Field(
        None, gt=0, description="Handshake timeout in seconds"
    )


class WaitStep(BaseStepConfig):
    """Configuration for wait step."""

    type: Literal["wait"] = "wait"
    seconds: int = Field(..., ge=0, description="Seconds to wait")
    message: Optional[str] = Field(None, description="Message to display while waiting")


class WaitForSyncStep(BaseStepConfig):
    """Configuration for wait_for_sync step.

    At least one of ``context_id`` / ``group_id`` is required (validated at
    runtime by the step executor — Pydantic can't express "one or both").

    * ``context_id`` — wait for ``contextStateHash`` (storage state) to
      converge across nodes.
    * ``group_id`` — wait for ``groupStateHash`` (governance state) to
      converge across nodes.
    * Both — wait for both. Useful for tests that change governance and
      expect state effects (e.g. removed member, verify their writes
      don't leak).
    """

    type: Literal["wait_for_sync"] = "wait_for_sync"
    context_id: Optional[str] = Field(
        None, description="Context ID to sync (poll contextStateHash)"
    )
    group_id: Optional[str] = Field(
        None, description="Group ID to sync (poll groupStateHash)"
    )
    nodes: list[str] = Field(..., description="Nodes to wait for sync")
    timeout: Optional[int] = Field(60, ge=1, description="Timeout in seconds")
    check_interval: Optional[float] = Field(
        2, gt=0, description="Steady-state polling cap in seconds (backoff ceiling)"
    )
    initial_check_interval: Optional[float] = Field(
        None,
        gt=0,
        description="Starting inter-attempt sleep for adaptive backoff (defaults to 0.05s)",
    )
    backoff_factor: Optional[float] = Field(
        None,
        ge=1,
        description="Geometric growth per missed check, capped at check_interval (defaults to 2.0)",
    )
    trigger_sync: Optional[bool] = Field(
        False, description="Trigger sync before waiting"
    )

    @model_validator(mode="after")
    def _at_least_one_id(self) -> "WaitForSyncStep":
        """Surface the "context_id or group_id required" rule at schema-validation
        time so `merobox validate` catches misconfigured workflows before run."""
        if self.context_id is None and self.group_id is None:
            raise ValueError(
                "wait_for_sync: at least one of 'context_id' or 'group_id' must be specified"
            )
        return self


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
    target: Optional[str] = Field("nodes", description="Target: 'image' or 'nodes'")
    description: Optional[str] = Field(None, description="Description of the script")


class PauseContainerStepConfig(BaseStepConfig):
    """Configuration for pause_container step."""

    type: Literal["pause_container"] = "pause_container"
    container: str = Field(..., description="Target container name")


class UnpauseContainerStepConfig(BaseStepConfig):
    """Configuration for unpause_container step."""

    type: Literal["unpause_container"] = "unpause_container"
    container: str = Field(..., description="Target container name")


class RestartContainerStepConfig(BaseStepConfig):
    """Configuration for restart_container step."""

    type: Literal["restart_container"] = "restart_container"
    container: str = Field(..., description="Target container name")
    wait_healthy: Optional[bool] = Field(
        True, description="Poll /admin-api/health until ready (default true)"
    )
    timeout: Optional[int] = Field(
        None, ge=1, description="Health-wait timeout in seconds"
    )


class DisconnectNodeStepConfig(BaseStepConfig):
    """Configuration for disconnect_node step."""

    type: Literal["disconnect_node"] = "disconnect_node"
    node: str = Field(..., description="Target node container name")
    network: Optional[str] = Field(
        None,
        description=(
            "Docker network to disconnect from. Defaults to the container's "
            "actual attached network (merobox-cluster / calimero_web / bridge)."
        ),
    )


class ConnectNodeStepConfig(BaseStepConfig):
    """Configuration for connect_node step."""

    type: Literal["connect_node"] = "connect_node"
    node: str = Field(..., description="Target node container name")
    network: Optional[str] = Field(
        None,
        description=(
            "Docker network to (re)connect to. Defaults to merobox-cluster "
            "(the modern multi-node default); set explicitly for legacy / "
            "single-node setups that use Docker's default bridge."
        ),
    )


class PartitionPeersStepConfig(BaseStepConfig):
    """Configuration for partition_peers step.

    Cuts libp2p between ``node`` and each container in ``peers`` while keeping
    every node RPC-reachable (unlike disconnect_node, which also severs the
    node's published-port RPC). Linux + iptables + passwordless sudo only.
    """

    type: Literal["partition_peers"] = "partition_peers"
    node: str = Field(..., description="Container to isolate from its peers")
    peers: list[str] = Field(
        ...,
        min_length=1,
        description="Peer container(s) to cut this node's libp2p traffic to/from",
    )


class HealPeersStepConfig(BaseStepConfig):
    """Configuration for heal_peers step — undoes a partition_peers (same args)."""

    type: Literal["heal_peers"] = "heal_peers"
    node: str = Field(..., description="Container to reconnect to its peers")
    peers: list[str] = Field(
        ...,
        min_length=1,
        description="Peer container(s) whose libp2p partition to lift",
    )


class InjectNetworkFaultStepConfig(BaseStepConfig):
    """Configuration for inject_network_fault step."""

    type: Literal["inject_network_fault"] = "inject_network_fault"
    container: str = Field(..., description="Target container name")
    fault: Literal["loss", "delay"] = Field(
        ..., description="Fault type — use disconnect_node for full partition"
    )
    duration: int = Field(..., ge=1, description="How long to hold the fault (seconds)")
    percent: Optional[float] = Field(
        None,
        gt=0,
        le=100,
        description="Loss percent in (0, 100] (required when fault=loss)",
    )
    ms: Optional[int] = Field(
        None, ge=1, description="Added delay in ms (required when fault=delay)"
    )
    interface: Optional[str] = Field(
        "eth0",
        pattern=r"^[A-Za-z0-9._-]{1,15}$",
        description="Container network interface (default: eth0; Linux naming rules)",
    )

    @model_validator(mode="after")
    def validate_fault_requires_arg(self) -> "InjectNetworkFaultStepConfig":
        """Each fault type needs its own arg — surface that at schema validation
        rather than waiting for the step to run.

        The numeric bounds (`percent` in (0, 100], `ms` >= 1) are already
        enforced by the Field constraints; this validator only checks
        cross-field presence based on `fault`.
        """
        if self.fault == "loss" and self.percent is None:
            raise ValueError(
                "inject_network_fault: 'percent' is required when fault=loss"
            )
        if self.fault == "delay" and self.ms is None:
            raise ValueError("inject_network_fault: 'ms' is required when fault=delay")
        return self


class AssertStep(BaseStepConfig):
    """Configuration for assert step."""

    type: Literal["assert"] = "assert"
    statements: list[Union[str, dict[str, Any]]] = Field(
        ..., description="List of assertion statements"
    )
    non_blocking: Optional[bool] = Field(
        None, description="If true, continue workflow on failure"
    )


class JsonAssertStep(BaseStepConfig):
    """Configuration for json_assert step."""

    type: Literal["json_assert"] = "json_assert"
    statements: list[Union[str, dict[str, Any]]] = Field(
        ..., description="List of JSON assertion statements"
    )


class AssertLogAbsentStepConfig(BaseStepConfig):
    """Configuration for assert_log_absent step."""

    type: Literal["assert_log_absent"] = "assert_log_absent"
    nodes: list[str] = Field(
        ..., description="Node names to scan. Empty list = all running nodes."
    )
    patterns: list[str] = Field(
        ..., description="Patterns whose presence in any node's logs fails the step"
    )
    regex: Optional[bool] = Field(
        False, description="If true, treat patterns as Python regex"
    )
    tail_lines: Optional[int] = Field(
        None, description="Only scan the last N lines per node"
    )
    case_sensitive: Optional[bool] = Field(
        True, description="If false, matching is case-insensitive"
    )


class AssertLogPresentStepConfig(BaseStepConfig):
    """Configuration for assert_log_present step."""

    type: Literal["assert_log_present"] = "assert_log_present"
    nodes: list[str] = Field(
        ..., description="Node names to scan. Empty list = all running nodes."
    )
    patterns: list[str] = Field(
        ..., description="Patterns each requiring at least min_matches hits"
    )
    regex: Optional[bool] = Field(
        False, description="If true, treat patterns as Python regex"
    )
    tail_lines: Optional[int] = Field(
        None, description="Only scan the last N lines per node"
    )
    case_sensitive: Optional[bool] = Field(
        True, description="If false, matching is case-insensitive"
    )
    min_matches: Optional[int] = Field(
        1, description="Required hits per pattern, aggregated across nodes"
    )


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
    file_path: str = Field(..., description="Path to the blob file")
    context_id: Optional[str] = Field(None, description="Context ID (optional)")


class DeleteBlobOnDiskStepConfig(BaseStepConfig):
    """Configuration for delete_blob_on_disk step."""

    type: Literal["delete_blob_on_disk"] = "delete_blob_on_disk"
    node: str = Field(..., description="Target node (== container name)")
    blob_id: str = Field(
        ...,
        description="Base58 blob id to delete (e.g. from list_application_versions)",
    )
    data_dir: Optional[str] = Field(
        None,
        description="CALIMERO_HOME inside the container (default /app/data)",
    )
    blobs_subdir: Optional[str] = Field(
        None,
        description="Blob-store subdir under <data_dir>/<node> (default 'blobs')",
    )
    missing_ok: Optional[bool] = Field(
        True,
        description="Treat a node that never held the blob as success (default true)",
    )


class DeleteBlobStepConfig(BaseStepConfig):
    """Configuration for delete_blob step (admin API, cascades chunked blobs)."""

    type: Literal["delete_blob"] = "delete_blob"
    node: str = Field(..., description="Target node")
    blob_id: str = Field(
        ...,
        description=(
            "Base58 (parent) blob id to delete (e.g. list_application_versions "
            "`blobId` or get_application `application.blob.bytecode`)"
        ),
    )
    missing_ok: Optional[bool] = Field(
        True,
        description="Treat a blob already absent on this node as success (default true)",
    )


class GetApplicationStepConfig(BaseStepConfig):
    """Configuration for get_application step."""

    type: Literal["get_application"] = "get_application"
    node: str = Field(..., description="Target node")
    application_id: str = Field(..., description="Application ID to read")


class CreateNamespaceStepConfig(BaseStepConfig):
    """Configuration for create_namespace step."""

    type: Literal["create_namespace", "create_group"] = "create_namespace"
    node: str = Field(..., description="Target node")
    application_id: str = Field(
        ..., description="Application ID for namespace creation"
    )
    app_key: Optional[str] = Field(
        None,
        description=(
            "Optional hex-encoded 32-byte bytecode blob id pinning the namespace "
            "to a specific installed application version (e.g. a blob_id from "
            "list_application_versions). Defaults to the application row's latest "
            "blob when omitted."
        ),
    )
    # A namespace's display name (if any) is set afterward via a
    # set_group_metadata step — there is no namespace-name field here. The
    # inherited `name` from BaseStepConfig is the step label, nothing more.


class CreateNamespaceInvitationStepConfig(BaseStepConfig):
    """Configuration for create_namespace_invitation step."""

    type: Literal["create_namespace_invitation", "create_group_invitation"] = (
        "create_namespace_invitation"
    )
    node: str = Field(..., description="Target node")
    namespace_id: Optional[str] = Field(None, description="Namespace ID to invite to")
    recursive: Optional[bool] = Field(False, description="Create recursive invitation")

    @model_validator(mode="after")
    def normalize_namespace_id(self) -> "CreateNamespaceInvitationStepConfig":
        # Backward compatibility: accept group_id as alias of namespace_id.
        group_id = getattr(self, "group_id", None)
        if not self.namespace_id and group_id:
            self.namespace_id = group_id
        if not self.namespace_id:
            raise ValueError(
                "Either 'namespace_id' or deprecated alias 'group_id' is required"
            )
        return self


class JoinNamespaceStepConfig(BaseStepConfig):
    """Configuration for join_namespace step."""

    type: Literal["join_namespace", "join_group"] = "join_namespace"
    node: str = Field(..., description="Target node")
    namespace_id: Optional[str] = Field(None, description="Namespace ID to join")
    invitation: str = Field(..., description="Namespace invitation data")

    @model_validator(mode="after")
    def normalize_namespace_id(self) -> "JoinNamespaceStepConfig":
        # Backward compatibility: accept group_id as alias of namespace_id.
        group_id = getattr(self, "group_id", None)
        if not self.namespace_id and group_id:
            self.namespace_id = group_id
        if not self.namespace_id:
            raise ValueError(
                "Either 'namespace_id' or deprecated alias 'group_id' is required"
            )
        return self


class ListNamespacesStepConfig(BaseStepConfig):
    """Configuration for list_namespaces step."""

    type: Literal["list_namespaces"] = "list_namespaces"
    node: str = Field(..., description="Target node")


class GetNamespaceIdentityStepConfig(BaseStepConfig):
    """Configuration for get_namespace_identity step."""

    type: Literal["get_namespace_identity"] = "get_namespace_identity"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(..., description="Namespace ID")


class CreateGroupInNamespaceStepConfig(BaseStepConfig):
    """Configuration for create_group_in_namespace step."""

    type: Literal["create_group_in_namespace"] = "create_group_in_namespace"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(..., description="Namespace ID")
    group_name: Optional[str] = Field(None, description="Optional group display name")
    visibility: Optional[str] = Field(
        None,
        description=(
            "Optional birth visibility for the subgroup: 'open' or 'restricted' "
            "(#2771). Absent ⇒ server default ('restricted'). When 'open', the "
            "subgroup is Open at creation time so tee_subgroup_admit skips it and "
            "no transient direct ReadOnlyTee row is written."
        ),
    )


class ListNamespaceGroupsStepConfig(BaseStepConfig):
    """Configuration for list_namespace_groups step."""

    type: Literal["list_namespace_groups"] = "list_namespace_groups"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(..., description="Namespace ID")


class ReparentGroupStepConfig(BaseStepConfig):
    """Configuration for reparent_group step.

    Replaces NestGroupStepConfig + UnnestGroupStepConfig in the strict
    group-tree refactor. Atomically moves `child_group_id` to a new
    parent within the same namespace; orphan state is no longer
    expressible.
    """

    type: Literal["reparent_group"] = "reparent_group"
    node: str = Field(..., description="Target node")
    child_group_id: str = Field(..., description="Group to move")
    new_parent_id: str = Field(..., description="New parent group ID")


class ListSubgroupsStepConfig(BaseStepConfig):
    """Configuration for list_subgroups step."""

    type: Literal["list_subgroups"] = "list_subgroups"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class AddGroupMembersStepConfig(BaseStepConfig):
    """Configuration for add_group_members step."""

    type: Literal["add_group_members"] = "add_group_members"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID to add members to")
    members: list[dict[str, str]] = Field(
        ..., description="List of members with identity and role"
    )


class RemoveGroupMembersStepConfig(BaseStepConfig):
    """Configuration for remove_group_members step."""

    type: Literal["remove_group_members"] = "remove_group_members"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID to remove members from")
    members: list[str] = Field(..., description="List of member public keys to remove")


class ListGroupMembersStepConfig(BaseStepConfig):
    """Configuration for list_group_members step."""

    type: Literal["list_group_members"] = "list_group_members"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class ListGroupContextsStepConfig(BaseStepConfig):
    """Configuration for list_group_contexts step."""

    type: Literal["list_group_contexts"] = "list_group_contexts"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class UpdateMemberRoleStepConfig(BaseStepConfig):
    """Configuration for update_member_role step."""

    type: Literal["update_member_role"] = "update_member_role"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Member public key")
    role: Literal[
        "Admin",
        "Member",
        "ReadOnly",
        "admin",
        "member",
        "read-only",
        "read_only",
        "readonly",
    ] = Field(..., description="New role for the member")


class SetMemberCapabilitiesStepConfig(BaseStepConfig):
    """Configuration for set_member_capabilities step."""

    type: Literal["set_member_capabilities"] = "set_member_capabilities"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Member public key")
    capabilities: int = Field(
        ..., ge=0, lt=2**32, description="Capability bitmask (u32)"
    )


class GetMemberCapabilitiesStepConfig(BaseStepConfig):
    """Configuration for get_member_capabilities step."""

    type: Literal["get_member_capabilities"] = "get_member_capabilities"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Member public key")


class SetMemberAutoFollowStepConfig(BaseStepConfig):
    """Configuration for set_member_auto_follow step.

    Toggles a member's per-group `auto_follow.contexts` /
    `auto_follow.subgroups` flags. Authorized by group admin (for any
    `member_id`) or by the target itself (self-setting); the apply path
    enforces admin-or-self (calimero-network/core#2422).
    """

    type: Literal["set_member_auto_follow"] = "set_member_auto_follow"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Target member public key")
    auto_follow_contexts: bool = Field(
        ..., description="Auto-join new contexts registered in this group"
    )
    auto_follow_subgroups: bool = Field(
        ..., description="Self-admit into nested subgroups under this group"
    )
    requester: Optional[str] = Field(
        None,
        description=(
            "Optional public key of the identity to act on behalf of "
            "(must be the group admin or the target itself); when omitted "
            "the server resolves an admin signing key it holds."
        ),
    )


class SetDefaultCapabilitiesStepConfig(BaseStepConfig):
    """Configuration for set_default_capabilities step."""

    type: Literal["set_default_capabilities"] = "set_default_capabilities"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    capabilities: int = Field(
        ..., ge=0, lt=2**32, description="Default capability bitmask (u32)"
    )


class SetSubgroupVisibilityStepConfig(BaseStepConfig):
    """Configuration for set_subgroup_visibility step.

    Sets a subgroup's visibility (`open` / `restricted`). When `open`,
    parent-group members holding `CAN_JOIN_OPEN_SUBGROUPS` are inherited
    as members of this subgroup. When `restricted`, membership requires
    explicit `add_group_members` (calimero-network/core#2256).
    """

    type: Literal["set_subgroup_visibility"] = "set_subgroup_visibility"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    visibility: Literal["open", "restricted", "Open", "Restricted"] = Field(
        ...,
        description="Subgroup visibility: open (inherits) or restricted (explicit only)",
    )


class SetDefaultVisibilityStepConfig(BaseStepConfig):
    """Configuration for the deprecated `set_default_visibility` step.

    Kept so workflows pinned to the pre-#2256 step name keep validating.
    Accepts the same fields as `SetSubgroupVisibilityStepConfig` and is
    dispatched to the same executor class.
    """

    type: Literal["set_default_visibility"] = "set_default_visibility"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    visibility: Literal["open", "restricted", "Open", "Restricted"] = Field(
        ...,
        description="Subgroup visibility: open (inherits) or restricted (explicit only)",
    )


class GetGroupInfoStepConfig(BaseStepConfig):
    """Configuration for get_group_info step."""

    type: Literal["get_group_info"] = "get_group_info"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class DeleteGroupStepConfig(BaseStepConfig):
    """Configuration for delete_group step."""

    type: Literal["delete_group"] = "delete_group"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID to delete")
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key, required when deleting a group with admin-guarded state",
    )


class DeleteNamespaceStepConfig(BaseStepConfig):
    """Configuration for delete_namespace step."""

    type: Literal["delete_namespace"] = "delete_namespace"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(..., description="Namespace ID to delete")
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key, required when deleting a namespace with admin-guarded state",
    )


class DeleteContextStepConfig(BaseStepConfig):
    """Configuration for delete_context step."""

    type: Literal["delete_context"] = "delete_context"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID to delete")
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key, required when deleting a context registered in a group",
    )


class UninstallApplicationStepConfig(BaseStepConfig):
    """Configuration for uninstall_application step."""

    type: Literal["uninstall_application"] = "uninstall_application"
    node: str = Field(..., description="Target node")
    application_id: str = Field(..., description="Application ID to uninstall")


class SetGroupMetadataStepConfig(BaseStepConfig):
    """Configuration for set_group_metadata step (admin-API group metadata).

    The metadata record's optional name comes from ``record_name`` (kept
    distinct from the inherited ``name`` field, which is the step label).
    """

    type: Literal["set_group_metadata"] = "set_group_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    record_name: Optional[str] = Field(
        None, description="Optional name for the metadata record"
    )
    data: Optional[dict[str, str]] = Field(
        None, description="Arbitrary string->string metadata map"
    )
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key; required when the group is admin-guarded",
    )


class GetGroupMetadataStepConfig(BaseStepConfig):
    """Configuration for get_group_metadata step."""

    type: Literal["get_group_metadata"] = "get_group_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class SetMemberMetadataStepConfig(BaseStepConfig):
    """Configuration for set_member_metadata step (admin-API member metadata)."""

    type: Literal["set_member_metadata"] = "set_member_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Member public key")
    record_name: Optional[str] = Field(
        None, description="Optional name for the metadata record"
    )
    data: Optional[dict[str, str]] = Field(
        None, description="Arbitrary string->string metadata map"
    )
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key; required when the group is admin-guarded",
    )


class GetMemberMetadataStepConfig(BaseStepConfig):
    """Configuration for get_member_metadata step."""

    type: Literal["get_member_metadata"] = "get_member_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    member_id: str = Field(..., description="Member public key")


class SetContextMetadataStepConfig(BaseStepConfig):
    """Configuration for set_context_metadata step (admin-API context metadata)."""

    type: Literal["set_context_metadata"] = "set_context_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    context_id: str = Field(..., description="Group-registered context ID")
    record_name: Optional[str] = Field(
        None, description="Optional name for the metadata record"
    )
    data: Optional[dict[str, str]] = Field(
        None, description="Arbitrary string->string metadata map"
    )
    requester: Optional[str] = Field(
        None,
        description="Optional admin public key; required when the group is admin-guarded",
    )


class GetContextMetadataStepConfig(BaseStepConfig):
    """Configuration for get_context_metadata step."""

    type: Literal["get_context_metadata"] = "get_context_metadata"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    context_id: str = Field(..., description="Group-registered context ID")


class UpdateGroupSettingsStepConfig(BaseStepConfig):
    """Configuration for update_group_settings step (currently exposes upgrade_policy)."""

    type: Literal["update_group_settings"] = "update_group_settings"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    # The server accepts only 'automatic' or 'lazy-on-access'; the former
    # 'coordinated' policy was removed and is now rejected on deserialize.
    upgrade_policy: Literal[
        "automatic",
        "lazy",
        "lazy-on-access",
        "lazy_on_access",
        "lazyonaccess",
    ] = Field(..., description="Group upgrade policy")


class DetachContextFromGroupStepConfig(BaseStepConfig):
    """Configuration for detach_context_from_group step."""

    type: Literal["detach_context_from_group"] = "detach_context_from_group"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID the context is currently in")
    context_id: str = Field(..., description="Context ID to detach from the group")


class SyncGroupStepConfig(BaseStepConfig):
    """Configuration for sync_group step (diagnostic governance sync trigger)."""

    type: Literal["sync_group"] = "sync_group"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID to trigger governance sync for")


class RegisterGroupSigningKeyStepConfig(BaseStepConfig):
    """Configuration for register_group_signing_key step."""

    type: Literal["register_group_signing_key"] = "register_group_signing_key"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    # Accepts either a 64-hex-char literal (32-byte PrivateKey) or a
    # `{{placeholder}}` template resolved at runtime. The {64} constraint
    # matches calimero_primitives::identity::PrivateKey's 32-byte size;
    # catches typos and trivially short values at YAML-load time, without
    # breaking workflows that inject the key via outputs from a prior step.
    #
    # SECURITY: prefer the `{{placeholder}}` form whenever the YAML is
    # committed to version control. Hardcoding a raw hex signing key in a
    # committed workflow file checks the actual key material into git
    # history — that's a credential leak. Capture the key from a prior
    # step's `outputs:`, or inject via `${ENV_VAR}` expansion, and refer
    # to it here as `{{key_name}}`.
    #
    # Note on `${ENV_VAR}` expansion: merobox's env-var substitution runs
    # BEFORE this Pydantic pattern is evaluated (see
    # `expand_env_vars` above, which walks the parsed YAML before
    # `validate_workflow_config`). So `signing_key: ${MY_KEY}` resolves
    # to the hex value at load-time and then passes the `[0-9a-fA-F]{64}`
    # alternative of this pattern — there's no need for the regex itself
    # to match `${...}` syntax.
    signing_key: str = Field(
        ...,
        pattern=r"^(\{\{[^}]+\}\}|[0-9a-fA-F]{64})$",
        description=(
            "Signing key as 64 hex chars (32-byte PrivateKey) or `{{placeholder}}` "
            "template. Prefer the template form — raw hex in committed YAML leaks "
            "key material via git history."
        ),
    )


class UpgradeGroupStepConfig(BaseStepConfig):
    """Configuration for upgrade_group step."""

    type: Literal["upgrade_group"] = "upgrade_group"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")
    target_application_id: str = Field(
        ..., description="Application ID to upgrade the group to"
    )
    cascade: bool = Field(
        False,
        description=(
            "When true, dispatch as a namespace cascade "
            "(CascadeTargetApplicationSet) instead of a per-group upgrade. "
            "Requires calimero-client-py >= 0.6.15."
        ),
    )


class CascadeNamespaceApplicationStepConfig(BaseStepConfig):
    """Configuration for cascade_namespace_application step."""

    type: Literal["cascade_namespace_application"] = "cascade_namespace_application"
    node: str = Field(..., description="Admin node emitting the cascade")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose descendants should cascade"
    )
    target_application_id: str = Field(
        ..., description="Application ID to cascade across the namespace"
    )


class GetGroupUpgradeStatusStepConfig(BaseStepConfig):
    """Configuration for get_group_upgrade_status step."""

    type: Literal["get_group_upgrade_status"] = "get_group_upgrade_status"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class GetCascadeStatusStepConfig(BaseStepConfig):
    """Configuration for get_cascade_status step."""

    type: Literal["get_cascade_status"] = "get_cascade_status"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose cascade subtree to inspect"
    )


class AbortMigrationStepConfig(BaseStepConfig):
    """Configuration for abort_migration step."""

    type: Literal["abort_migration"] = "abort_migration"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose in-flight migration to abort"
    )


class AssertCascadeCompleteStepConfig(BaseStepConfig):
    """Configuration for assert_cascade_complete step."""

    type: Literal["assert_cascade_complete"] = "assert_cascade_complete"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose cascade must complete"
    )
    timeout_seconds: Union[int, float] = Field(
        30,
        gt=0,
        description="Max seconds to poll before failing the assertion",
    )
    poll_interval: Union[int, float] = Field(
        2.0,
        gt=0,
        description="Seconds between get_cascade_status polls",
    )

    @field_validator("timeout_seconds", "poll_interval", mode="before")
    @classmethod
    def _reject_bool_str_and_non_finite(cls, v: Any) -> Any:
        # mode="before" so we see the raw value: Pydantic's lax mode would
        # otherwise coerce bool -> int (hiding `true` (=1) from the isinstance
        # check) and str -> number (accepting "30"). The runtime
        # AssertCascadeCompleteStep._validate_field_types rejects both bools
        # and non-(int|float) values, so mirror that here, plus NaN/inf which
        # would slip past `gt=0` and make the poll deadline non-finite.
        if isinstance(v, bool):
            raise ValueError("must be a number, not a boolean")
        if not isinstance(v, (int, float)):
            raise ValueError("must be an int or float, not a string")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


class GetMigrationStatusStepConfig(BaseStepConfig):
    """Configuration for get_migration_status step."""

    type: Literal["get_migration_status"] = "get_migration_status"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose migration rollup to read"
    )


class AssertMigrationCompleteStepConfig(BaseStepConfig):
    """Configuration for assert_migration_complete step."""

    type: Literal["assert_migration_complete"] = "assert_migration_complete"
    node: str = Field(..., description="Target node")
    namespace_id: str = Field(
        ..., description="Namespace (root group) whose migration must complete"
    )
    timeout_seconds: Union[int, float] = Field(
        30,
        gt=0,
        description="Max seconds to poll before failing the assertion",
    )
    poll_interval: Union[int, float] = Field(
        2.0,
        gt=0,
        description="Seconds between get_migration_status polls",
    )

    @field_validator("timeout_seconds", "poll_interval", mode="before")
    @classmethod
    def _reject_bool_str_and_non_finite(cls, v: Any) -> Any:
        # mode="before" so we see the raw value, mirroring
        # AssertMigrationCompleteStep._validate_field_types: reject bools (which
        # lax mode would coerce to 1/0), strings (coerced to numbers), and
        # NaN/inf (which would slip past `gt=0` and make the deadline
        # non-finite so the poll loop never times out).
        if isinstance(v, bool):
            raise ValueError("must be a number, not a boolean")
        if not isinstance(v, (int, float)):
            raise ValueError("must be an int or float, not a string")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


class ResyncContextStepConfig(BaseStepConfig):
    """Configuration for resync_context step."""

    type: Literal["resync_context"] = "resync_context"
    node: str = Field(..., description="Target node")
    context_id: str = Field(..., description="Context ID to resync from a peer")
    force: Optional[bool] = Field(
        False,
        description=(
            "Discard local DAG heads when resyncing. Must be true when the "
            "context still holds local heads (the resync overwrites them)."
        ),
    )


class ListApplicationVersionsStepConfig(BaseStepConfig):
    """Configuration for list_application_versions step."""

    type: Literal["list_application_versions"] = "list_application_versions"
    node: str = Field(..., description="Target node")
    application_id: str = Field(
        ..., description="Application ID whose retained bytecode versions to list"
    )


class RetryGroupUpgradeStepConfig(BaseStepConfig):
    """Configuration for retry_group_upgrade step."""

    type: Literal["retry_group_upgrade"] = "retry_group_upgrade"
    node: str = Field(..., description="Target node")
    group_id: str = Field(..., description="Group ID")


class CreateMeshStep(BaseStepConfig):
    """Configuration for create_mesh step.

    Uses the group-based flow: creates a context (with auto-created group),
    then creates group invitations and joins for each node.
    """

    type: Literal["create_mesh"] = "create_mesh"
    context_node: str = Field(..., description="Node to create context on")
    application_id: str = Field(..., description="Application ID")
    nodes: list[str] = Field(..., description="List of nodes to include in mesh")
    params: Optional[str] = Field(None, description="Initialization params JSON string")


class FuzzyTestStep(BaseStepConfig):
    """Configuration for fuzzy_test step."""

    type: Literal["fuzzy_test"] = "fuzzy_test"
    duration_minutes: Union[int, float] = Field(
        ..., gt=0, description="Duration in minutes for the fuzzy test"
    )
    context_id: str = Field(..., description="Context ID")
    nodes: list[dict[str, Any]] = Field(
        ..., description="List of nodes with name and executor_key"
    )
    operations: list[dict[str, Any]] = Field(
        ..., description="List of operations to execute"
    )


# Module-level mapping of step types to their Pydantic models
# This avoids recreating the dict on every validation call
STEP_TYPE_MODELS: dict[str, type[BaseStepConfig]] = {
    "install_application": InstallApplicationStep,
    "create_context": CreateContextStep,
    "create_identity": CreateIdentityStep,
    "create_namespace": CreateNamespaceStepConfig,
    "create_namespace_invitation": CreateNamespaceInvitationStepConfig,
    "join_namespace": JoinNamespaceStepConfig,
    # Deprecated aliases
    "create_group": CreateNamespaceStepConfig,
    "create_group_invitation": CreateNamespaceInvitationStepConfig,
    "join_group": JoinNamespaceStepConfig,
    "invite": InviteStep,
    "invite_identity": InviteStep,
    "invite_open": InviteStep,
    "join": JoinStep,
    "join_context": JoinContextStepConfig,
    "join_subgroup_inheritance": JoinSubgroupInheritanceStepConfig,
    "join_open": JoinStep,
    "list_namespaces": ListNamespacesStepConfig,
    "get_namespace_identity": GetNamespaceIdentityStepConfig,
    "create_group_in_namespace": CreateGroupInNamespaceStepConfig,
    "list_namespace_groups": ListNamespaceGroupsStepConfig,
    "reparent_group": ReparentGroupStepConfig,
    "list_subgroups": ListSubgroupsStepConfig,
    "add_group_members": AddGroupMembersStepConfig,
    "remove_group_members": RemoveGroupMembersStepConfig,
    "list_group_members": ListGroupMembersStepConfig,
    "list_group_contexts": ListGroupContextsStepConfig,
    "update_member_role": UpdateMemberRoleStepConfig,
    "set_member_capabilities": SetMemberCapabilitiesStepConfig,
    "set_member_auto_follow": SetMemberAutoFollowStepConfig,
    "get_member_capabilities": GetMemberCapabilitiesStepConfig,
    "set_default_capabilities": SetDefaultCapabilitiesStepConfig,
    "set_default_visibility": SetDefaultVisibilityStepConfig,
    "set_subgroup_visibility": SetSubgroupVisibilityStepConfig,
    "get_group_info": GetGroupInfoStepConfig,
    "delete_group": DeleteGroupStepConfig,
    "delete_namespace": DeleteNamespaceStepConfig,
    "delete_context": DeleteContextStepConfig,
    "uninstall_application": UninstallApplicationStepConfig,
    "set_group_metadata": SetGroupMetadataStepConfig,
    "get_group_metadata": GetGroupMetadataStepConfig,
    "set_member_metadata": SetMemberMetadataStepConfig,
    "get_member_metadata": GetMemberMetadataStepConfig,
    "set_context_metadata": SetContextMetadataStepConfig,
    "get_context_metadata": GetContextMetadataStepConfig,
    "update_group_settings": UpdateGroupSettingsStepConfig,
    "detach_context_from_group": DetachContextFromGroupStepConfig,
    "sync_group": SyncGroupStepConfig,
    "register_group_signing_key": RegisterGroupSigningKeyStepConfig,
    "upgrade_group": UpgradeGroupStepConfig,
    "cascade_namespace_application": CascadeNamespaceApplicationStepConfig,
    "get_cascade_status": GetCascadeStatusStepConfig,
    "assert_cascade_complete": AssertCascadeCompleteStepConfig,
    "abort_migration": AbortMigrationStepConfig,
    "get_migration_status": GetMigrationStatusStepConfig,
    "assert_migration_complete": AssertMigrationCompleteStepConfig,
    "resync_context": ResyncContextStepConfig,
    "list_application_versions": ListApplicationVersionsStepConfig,
    "get_group_upgrade_status": GetGroupUpgradeStatusStepConfig,
    "retry_group_upgrade": RetryGroupUpgradeStepConfig,
    "call": CallStep,
    "login": LoginStepConfig,
    "refresh": RefreshStepConfig,
    "ws_connect": WebSocketConnectStepConfig,
    "ws_subscribe": WebSocketConnectStepConfig,
    "wait": WaitStep,
    "wait_for_sync": WaitForSyncStep,
    "repeat": RepeatStep,
    "parallel": ParallelStep,
    "script": ScriptStep,
    "pause_container": PauseContainerStepConfig,
    "unpause_container": UnpauseContainerStepConfig,
    "restart_container": RestartContainerStepConfig,
    "disconnect_node": DisconnectNodeStepConfig,
    "connect_node": ConnectNodeStepConfig,
    "partition_peers": PartitionPeersStepConfig,
    "heal_peers": HealPeersStepConfig,
    "inject_network_fault": InjectNetworkFaultStepConfig,
    "assert": AssertStep,
    "json_assert": JsonAssertStep,
    "assert_log_absent": AssertLogAbsentStepConfig,
    "assert_log_present": AssertLogPresentStepConfig,
    "get_proposal": GetProposalStep,
    "list_proposals": ListProposalsStep,
    "get_proposal_approvers": GetProposalApproversStep,
    "upload_blob": UploadBlobStep,
    "delete_blob_on_disk": DeleteBlobOnDiskStepConfig,
    "delete_blob": DeleteBlobStepConfig,
    "get_application": GetApplicationStepConfig,
    "create_mesh": CreateMeshStep,
    "fuzzy_test": FuzzyTestStep,
}


# ---------------------------------------------------------------------------
# Topology configurations
# ---------------------------------------------------------------------------
#
# The default cluster mode (sibling addresses on a single Docker bridge,
# wired via `_wire_cluster_bootstrap_peers`) is enough for tests that
# just need two nodes talking. Some tests — specifically the relay-
# reservation recovery code path in core#2446 — need a topology where
# nodes physically cannot reach each other directly and MUST go
# through a relay. That's what `topology: { type: nat }` provides.
#
# Schema design: small enum + bag of mode-specific fields. New modes
# (e.g. `mesh` with multiple relays, `wireguard`-tunneled, etc.) plug
# in as a new variant of `TopologyConfig.type` rather than overloading
# the existing one. Mutually exclusive with the normal cluster-mode
# `nodes:` wiring — the executor branches on `topology` being present.


class NatBootNodeConfig(BaseModel):
    """Settings for the dedicated relay/boot-node container that sits
    on the public side of a NAT topology.

    The boot-node binary (calimero-network/boot-node) is the relay
    server — merod itself only ships the relay *client* behaviour,
    so the NAT topology can't use a stock merod container for this
    role. The image is built on-demand by merobox from the released
    `boot-node-x86_64-unknown-linux` asset unless an explicit one
    is supplied via `image`.
    """

    image: Optional[str] = Field(
        None,
        description=(
            "Docker image for the boot-node container. When unset, "
            "merobox builds one from the latest released "
            "calimero-network/boot-node binary on first use and "
            "caches it locally as `merobox/boot-node:local`."
        ),
    )
    keypair: Optional[str] = Field(
        None,
        description=(
            "Optional path to a libp2p keypair JSON to mount into the "
            "boot-node container. When unset the boot-node generates "
            "a fresh keypair on each startup — fine for one-shot CI "
            "runs, less so for tests that need a stable peer ID "
            "across container restarts."
        ),
    )


class NatTopologyConfig(BaseModel):
    """Two-bridge topology with a public boot-node, a NAT gateway,
    and N client nodes on an `--internal` LAN bridge.

    Why two bridges
    ---------------

    The client nodes' Docker network is created with `--internal`,
    which means it has no route to anything outside Docker (no
    default gateway, no NAT to the host). The boot-node sits on a
    regular bridge that does have outside connectivity. A separate
    gateway container straddles both bridges and runs iptables to
    NAT packets from the LAN bridge out to the public bridge.

    With this setup the clients physically cannot dial each other
    via /ip4 of the public bridge — they have to register a relay
    reservation on the boot-node and accept relayed traffic
    through it. That's the code path #2446 fixed.

    NAT modes
    ---------

    `cone` — `iptables MASQUERADE` (the default Docker NAT behaviour).
    Outbound port mapping is consistent per (source IP, source port);
    a remote peer that observes a client's NAT'd address from one
    exchange can use the same address to reach the client from
    another exchange. Good for testing the happy-path relay
    recovery flow; libp2p's DCUtR hole-punching may also succeed
    here.

    `symmetric` — `iptables MASQUERADE --random-fully` (when the
    kernel supports it). Outbound port is randomised per
    destination, so no STUN-style port prediction works. DCUtR
    hole-punching fails reliably; the client is reachable ONLY
    via the relay. The strictest test of the relay-only path.
    """

    type: Literal["nat"] = "nat"
    nat_mode: Literal["cone", "symmetric"] = Field(
        "cone",
        description=(
            "NAT translation mode for the gateway container. `cone` "
            "uses plain MASQUERADE; `symmetric` adds --random-fully "
            "so port prediction fails. See class doc for the "
            "trade-offs."
        ),
    )
    boot_node: NatBootNodeConfig = Field(
        default_factory=NatBootNodeConfig,
        description=(
            "Configuration for the boot-node container. See "
            "`NatBootNodeConfig` for fields; defaults are usable "
            "out of the box (auto-built image, fresh keypair)."
        ),
    )


# `TopologyConfig` is a bare alias today, not a discriminated
# union. There's only one topology variant (`NatTopologyConfig`),
# and a single-element Union is idiomatically equivalent + draws
# type-checker warnings, so we don't pay that cost yet.
#
# When the SECOND topology variant lands (e.g. `MeshTopologyConfig`
# with multiple relays, `WireguardTopologyConfig`, etc.) the
# migration is NOT just `TopologyConfig = Union[A, B]`. Pydantic
# needs an explicit `Field(discriminator=...)` so it can pick the
# right class from the YAML `type:` value — without that, every
# variant's optional fields would be treated as candidates and
# parsing errors would be unhelpfully vague. Migration steps:
#
# 1. Each variant gets a literal type tag: `type: Literal["nat"]`
#    on `NatTopologyConfig`, `type: Literal["mesh"]` on the new
#    `MeshTopologyConfig`, etc. The tag is what Pydantic
#    dispatches on.
# 2. Redefine the alias as a discriminated Union:
#    `TopologyConfig = Annotated[Union[NatTopologyConfig, ...],
#    Field(discriminator="type")]`
# 3. Workflow YAMLs already use a `type:` key, so no schema
#    migration on the operator side.
#
# Until that work happens, the bare alias keeps call-sites
# future-proof in annotation form without paying the lint cost.
TopologyConfig = NatTopologyConfig


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
    restart: Optional[bool] = Field(False, description="Restart nodes at the beginning")

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
    auth_use_cached: Optional[bool] = Field(False, description="Use cached auth tokens")
    webui_use_cached: Optional[bool] = Field(False, description="Use cached WebUI")
    auth_mode: Optional[str] = Field(None, description="Auth mode (e.g., embedded)")

    # Logging options
    log_level: Optional[str] = Field("debug", description="Log level for nodes")
    rust_backtrace: Optional[str] = Field("0", description="RUST_BACKTRACE setting")

    # E2E and testing options
    e2e_mode: Optional[bool] = Field(False, description="Enable E2E testing mode")
    bootstrap_nodes: Optional[list[str]] = Field(
        None, description="Bootstrap nodes to connect to"
    )
    preserve_default_bootstrap: bool = Field(
        False,
        description=(
            "When `--e2e-mode` is in effect, skip clearing `bootstrap.nodes` "
            "in apply_e2e_defaults. The default boot-node list that "
            "`merod init` writes (the public devnet boot-node from "
            "calimero-network/core) is preserved instead. Useful for "
            "workflows that need a stable rendezvous server outside the "
            "test cluster but don't want to hard-code the exact boot-node "
            "addresses in the workflow YAML."
        ),
    )

    # Topology — see TopologyConfig types above for the full schema.
    # Default (`None`) means "use the normal cluster-mode wiring": one
    # Docker bridge, every node dials its siblings directly. Setting
    # this to a `NatTopologyConfig` switches startup to the multi-
    # bridge NAT path; `nodes:` is reinterpreted as "client nodes",
    # the boot-node and gateway are spawned automatically, and the
    # cluster-bootstrap-wiring step is skipped (clients point at the
    # boot-node instead). Mutually exclusive with the cluster-wiring
    # codepath — picking one disables the other.
    topology: Optional[TopologyConfig] = Field(
        None,
        description=(
            "Multi-bridge / NAT topology selector. Default cluster mode "
            "(all nodes on one Docker bridge) is enough for tests that "
            "just need peer-to-peer connectivity; NAT topology is "
            "required to exercise the relay-reservation recovery code "
            "in calimero-network/core#2446 because that code path is "
            "dead unless clients physically can't dial each other."
        ),
    )


def _format_pydantic_error(error: dict[str, Any]) -> str:
    """Format a single Pydantic validation error into a readable message."""
    loc = ".".join(str(x) for x in error.get("loc", []))
    msg = error.get("msg", "Unknown error")

    if loc:
        return f"{loc}: {msg}"
    return msg


def validate_workflow_step(step: dict[str, Any], step_index: int) -> list[str]:
    """
    Validate a single workflow step and return a list of validation errors.

    Args:
        step: The step configuration dictionary
        step_index: The index of the step (for error messages)

    Returns:
        List of validation error messages (empty if valid)
    """
    # Guard against non-dict steps (e.g., null or scalar values in YAML)
    if not isinstance(step, dict):
        return [f"Step {step_index}: Expected a mapping but got {type(step).__name__}"]

    errors = []
    step_name = step.get("name", f"Step {step_index + 1}")
    step_type = step.get("type")

    if not step_type:
        errors.append(
            f"Step '{step_name}' (index {step_index}): Missing required field 'type'"
        )
        return errors

    if step_type not in VALID_STEP_TYPES:
        errors.append(
            f"Step '{step_name}' (index {step_index}): Invalid step type '{step_type}'. "
            f"Valid types are: {', '.join(sorted(VALID_STEP_TYPES))}"
        )
        return errors

    # Type-specific validation using module-level mapping
    model_class = STEP_TYPE_MODELS.get(step_type)
    if model_class:
        try:
            model_class.model_validate(step)
        except ValidationError as e:
            # Use structured error access for reliable parsing
            for err in e.errors():
                formatted = _format_pydantic_error(err)
                errors.append(f"Step '{step_name}' (index {step_index}): {formatted}")
        except Exception as e:
            errors.append(f"Step '{step_name}' (index {step_index}): {str(e)}")

    # Recursively validate nested steps (for repeat and parallel)
    if step_type == "repeat":
        # Use `or []` to handle null values when key exists with no value
        nested_steps = step.get("steps") or []
        for i, nested_step in enumerate(nested_steps):
            nested_errors = validate_workflow_step(nested_step, i)
            for err in nested_errors:
                errors.append(f"Step '{step_name}' (index {step_index}) -> {err}")

    elif step_type == "parallel":
        # Use `or []` to handle null values when key exists with no value
        groups = step.get("groups") or []
        for g_idx, group in enumerate(groups):
            # Guard against non-dict group elements
            if not isinstance(group, dict):
                errors.append(
                    f"Step '{step_name}' (index {step_index}): "
                    f"Group {g_idx} must be a mapping but got {type(group).__name__}"
                )
                continue
            group_name = group.get("name", f"Group {g_idx + 1}")
            # Use `or []` for nested steps as well
            nested_steps = group.get("steps") or []
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
    except ValidationError as e:
        # Use structured error access for reliable parsing
        for err in e.errors():
            formatted = _format_pydantic_error(err)
            errors.append(f"Workflow config: {formatted}")
    except Exception as e:
        errors.append(f"Workflow config: {str(e)}")

    # Check for required nodes configuration
    if not config.get("nodes") and not config.get("remote_nodes"):
        errors.append(
            "Workflow config: At least one of 'nodes' or 'remote_nodes' must be specified"
        )

    # Validate each step (handle None value when key exists but has no value)
    steps = config.get("steps") or []
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

        # Perform schema validation unless explicitly skipped or in validate_only mode
        # (validate_only mode is used by the validate command which has its own validator)
        if not skip_schema_validation and not validate_only:
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
                "name": "Create Namespace on Node 1",
                "type": "create_namespace",
                "node": "calimero-node-1",
                "application_id": "{{app_id}}",
                "outputs": {"namespace_id": "namespaceId"},
            },
            {
                "name": "Create Context on Node 1",
                "type": "create_context",
                "node": "calimero-node-1",
                "application_id": "{{app_id}}",
                "group_id": "{{namespace_id}}",
                "outputs": {"context_id": "id", "member_public_key": "memberPublicKey"},
            },
            {
                "name": "Create Identity on Node 2",
                "type": "create_identity",
                "node": "calimero-node-2",
                "outputs": {"public_key": "publicKey"},
            },
            {
                "name": "Create Namespace Invitation",
                "type": "create_namespace_invitation",
                "node": "calimero-node-1",
                "namespace_id": "{{namespace_id}}",
                "outputs": {"invitation": "invitation"},
            },
            {
                "name": "Join Namespace from Node 2",
                "type": "join_namespace",
                "node": "calimero-node-2",
                "namespace_id": "{{namespace_id}}",
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
