"""
Namespace-related workflow step executors.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class ListNamespacesStep(BaseStep):
    """List namespaces on a node."""

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            list_namespaces = getattr(client, "list_namespaces", None)
            if callable(list_namespaces):
                result = ok(list_namespaces())
            else:
                # Backward compatibility for older client versions.
                result = ok(client.list_groups())
        except Exception as e:
            result = fail("list_namespaces failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to list namespaces on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"namespaces_{node_name}"] = result["data"]
        return True


class GetNamespaceIdentityStep(BaseStep):
    """Get namespace identity for a namespace ID."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "publicKey",
                "namespace_public_key_{node_name}",
                "Namespace-scoped public key for this node (group admin)",
            ),
            (
                "namespaceId",
                "namespace_id_echo_{node_name}",
                "Namespace ID echoed back by the server",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            get_namespace_identity = getattr(client, "get_namespace_identity", None)
            if callable(get_namespace_identity):
                result = ok(get_namespace_identity(namespace_id=namespace_id))
            else:
                # No strict fallback available in old clients.
                raise RuntimeError(
                    "get_namespace_identity is not available in current client"
                )
        except Exception as e:
            result = fail("get_namespace_identity failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to get namespace identity on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"namespace_identity_{node_name}"] = result["data"]
        # Only call _export_variables when outputs are configured. Without this
        # guard, the base class prints a "No outputs configured" warning for
        # every pre-existing workflow using get_namespace_identity without an
        # outputs: section.
        if "outputs" in self.config:
            self._export_variables(result["data"], node_name, dynamic_values)
        return True


class CreateGroupInNamespaceStep(BaseStep):
    """Create a group within a namespace."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id", "group_alias"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id", "group_alias"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
        )
        group_alias = self._resolve_dynamic_value(
            self.config["group_alias"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            create_group_in_namespace = getattr(
                client, "create_group_in_namespace", None
            )
            if callable(create_group_in_namespace):
                result = ok(
                    create_group_in_namespace(
                        namespace_id=namespace_id,
                        group_alias=group_alias,
                    )
                )
            else:
                # No strict fallback available in old clients.
                raise RuntimeError(
                    "create_group_in_namespace is not available in current client"
                )
        except Exception as e:
            result = fail("create_group_in_namespace failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]Failed to create group in namespace on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False
        workflow_results[f"group_in_namespace_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(f"[green]✓ Created group in namespace on {node_name}[/green]")
        if expected_failure:
            self._report_unexpected_success()
        return True


class ListNamespaceGroupsStep(BaseStep):
    """List groups within a namespace."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            list_namespace_groups = getattr(client, "list_namespace_groups", None)
            if callable(list_namespace_groups):
                result = ok(list_namespace_groups(namespace_id=namespace_id))
            else:
                # Backward compatibility: best-effort fallback to generic group listing.
                result = ok(client.list_groups())
        except Exception as e:
            result = fail("list_namespace_groups failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to list namespace groups on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            return False
        workflow_results[f"namespace_groups_{node_name}"] = result["data"]
        return True
