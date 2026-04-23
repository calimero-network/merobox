"""
Group management workflow step executors.

Steps for managing group membership, roles, capabilities, and lifecycle.
"""

import json
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class RemoveGroupMembersStep(BaseStep):
    """Remove members from a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "members"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("group_id"), str):
            raise ValueError(f"Step '{step_name}': 'group_id' must be a string")
        if not isinstance(self.config.get("members"), list):
            raise ValueError(f"Step '{step_name}': 'members' must be a list")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        members = [
            (
                self._resolve_dynamic_value(m, workflow_results, dynamic_values)
                if isinstance(m, str)
                else m
            )
            for m in self.config["members"]
        ]
        members_json = json.dumps(members)

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.remove_group_members(
                group_id=group_id, members_json=members_json
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("remove_group_members failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to remove group members on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"remove_group_members_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Removed {len(members)} member(s) from group {group_id} on {node_name}[/green]"
        )
        return True


class ListGroupMembersStep(BaseStep):
    """List members of a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [("members", "members_{node_name}", "List of group members")]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.list_group_members(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("list_group_members failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]list_group_members failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"members_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Listed members for group {group_id} on {node_name}[/green]"
        )
        return True


class UpdateMemberRoleStep(BaseStep):
    """Update a member's role in a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "member_id", "role"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "member_id", "role"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        member_id = self._resolve_dynamic_value(
            self.config["member_id"], workflow_results, dynamic_values
        )
        role = self._resolve_dynamic_value(
            self.config["role"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.update_member_role(
                group_id=group_id, member_id=member_id, role=role
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("update_member_role failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]update_member_role failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"update_member_role_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Updated role to '{role}' for {member_id} in group {group_id} on {node_name}[/green]"
        )
        return True


class SetMemberCapabilitiesStep(BaseStep):
    """Set capabilities for a specific member in a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "member_id", "capabilities"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "member_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if not isinstance(self.config.get("capabilities"), int):
            raise ValueError(f"Step '{step_name}': 'capabilities' must be an integer")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        member_id = self._resolve_dynamic_value(
            self.config["member_id"], workflow_results, dynamic_values
        )
        capabilities = self.config["capabilities"]

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.set_member_capabilities(
                group_id=group_id, member_id=member_id, capabilities=capabilities
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("set_member_capabilities failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]set_member_capabilities failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"set_member_capabilities_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set capabilities for {member_id} in group {group_id} on {node_name}[/green]"
        )
        return True


class GetMemberCapabilitiesStep(BaseStep):
    """Get capabilities for a specific member in a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "member_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "member_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [("capabilities", "capabilities_{node_name}", "Member capabilities")]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        member_id = self._resolve_dynamic_value(
            self.config["member_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.get_member_capabilities(
                group_id=group_id, member_id=member_id
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("get_member_capabilities failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]get_member_capabilities failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"capabilities_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Got capabilities for {member_id} in group {group_id} on {node_name}[/green]"
        )
        return True


class SetDefaultCapabilitiesStep(BaseStep):
    """Set default capabilities for new members in a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "capabilities"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("group_id"), str):
            raise ValueError(f"Step '{step_name}': 'group_id' must be a string")
        if not isinstance(self.config.get("capabilities"), int):
            raise ValueError(f"Step '{step_name}': 'capabilities' must be an integer")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        capabilities = self.config["capabilities"]

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.set_default_capabilities(
                group_id=group_id, capabilities=capabilities
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("set_default_capabilities failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]set_default_capabilities failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"set_default_capabilities_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set default capabilities for group {group_id} on {node_name}[/green]"
        )
        return True


class SetDefaultVisibilityStep(BaseStep):
    """Set default context visibility for a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "visibility"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "visibility"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        visibility = self._resolve_dynamic_value(
            self.config["visibility"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.set_default_visibility(
                group_id=group_id, visibility=visibility
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("set_default_visibility failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]set_default_visibility failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"set_default_visibility_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set default visibility to '{visibility}' for group {group_id} on {node_name}[/green]"
        )
        return True


class GetGroupInfoStep(BaseStep):
    """Get detailed info about a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [("group_info", "group_info_{node_name}", "Group info")]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.get_group_info(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("get_group_info failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]get_group_info failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"group_info_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(f"[green]✓ Got info for group {group_id} on {node_name}[/green]")
        return True


class ListGroupContextsStep(BaseStep):
    """List contexts in a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [("contexts", "contexts_{node_name}", "List of group contexts")]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.list_group_contexts(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("list_group_contexts failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]list_group_contexts failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"contexts_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Listed contexts for group {group_id} on {node_name}[/green]"
        )
        return True


class DeleteGroupStep(BaseStep):
    """Delete a group.

    The optional `requester` field takes an admin public key. When deleting a
    group that has members (or is in an admin-guarded state), the server
    requires an explicit admin requester. Omit `requester` for groups that
    don't require one.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        requester = self.config.get("requester")
        if requester is not None and not isinstance(requester, str):
            raise ValueError(
                f"Step '{step_name}': 'requester' must be a string if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        requester_raw = self.config.get("requester")
        requester = (
            self._resolve_dynamic_value(requester_raw, workflow_results, dynamic_values)
            if requester_raw is not None
            else None
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.delete_group(group_id=group_id, requester=requester)
            result = ok(api_result)
        except Exception as e:
            result = fail("delete_group failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]delete_group failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"delete_group_{node_name}"] = result["data"]
        console.print(f"[green]✓ Deleted group {group_id} on {node_name}[/green]")
        return True


class DeleteNamespaceStep(BaseStep):
    """Delete a namespace.

    The optional `requester` field takes an admin public key. When deleting a
    namespace with admin-guarded state, the server requires an explicit admin
    requester.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        requester = self.config.get("requester")
        if requester is not None and not isinstance(requester, str):
            raise ValueError(
                f"Step '{step_name}': 'requester' must be a string if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
        )
        requester_raw = self.config.get("requester")
        requester = (
            self._resolve_dynamic_value(requester_raw, workflow_results, dynamic_values)
            if requester_raw is not None
            else None
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.delete_namespace(
                namespace_id=namespace_id, requester=requester
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("delete_namespace failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]delete_namespace failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"delete_namespace_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Deleted namespace {namespace_id} on {node_name}[/green]"
        )
        return True


class DeleteContextStep(BaseStep):
    """Delete a context.

    The optional `requester` field takes an admin public key. Deleting a
    context that is registered in a group requires an admin requester
    (core/crates/context/src/handlers/delete_context.rs:54-68). Contexts
    not attached to a group can be deleted without a requester.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "context_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        requester = self.config.get("requester")
        if requester is not None and not isinstance(requester, str):
            raise ValueError(
                f"Step '{step_name}': 'requester' must be a string if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        requester_raw = self.config.get("requester")
        requester = (
            self._resolve_dynamic_value(requester_raw, workflow_results, dynamic_values)
            if requester_raw is not None
            else None
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.delete_context(
                context_id=context_id, requester=requester
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("delete_context failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]delete_context failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"delete_context_{node_name}"] = result["data"]
        console.print(f"[green]✓ Deleted context {context_id} on {node_name}[/green]")
        return True


class UninstallApplicationStep(BaseStep):
    """Uninstall an application."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "application_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.uninstall_application(app_id=application_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("uninstall_application failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]uninstall_application failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"uninstall_application_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Uninstalled application {application_id} on {node_name}[/green]"
        )
        return True
