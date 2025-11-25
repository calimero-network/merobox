"""
Create mesh step executor - Creates a context and connects multiple nodes to it.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import DEFAULT_PROTOCOL
from merobox.commands.identity import (
    generate_identity_via_admin_api,
    invite_identity_via_admin_api,
)
from merobox.commands.join import join_context_via_admin_api
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.utils import console, extract_nested_data, get_node_rpc_url


class CreateMeshStep(BaseStep):
    """Execute a create mesh step that creates a context and connects multiple nodes."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["context_node", "application_id", "nodes"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate context_node is a string
        if not isinstance(self.config.get("context_node"), str):
            raise ValueError(f"Step '{step_name}': 'context_node' must be a string")

        # Validate application_id is a string
        if not isinstance(self.config.get("application_id"), str):
            raise ValueError(f"Step '{step_name}': 'application_id' must be a string")

        # Validate nodes is a list
        nodes = self.config.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError(f"Step '{step_name}': 'nodes' must be a list")
        if len(nodes) == 0:
            raise ValueError(f"Step '{step_name}': 'nodes' must not be empty")

        # Validate each node in the list is a string
        for i, node in enumerate(nodes):
            if not isinstance(node, str):
                raise ValueError(f"Step '{step_name}': 'nodes[{i}]' must be a string")

        # Validate that at least one node is different from context_node
        context_node = self.config.get("context_node")
        if context_node and isinstance(context_node, str):
            distinct_nodes = [n for n in nodes if n != context_node]
            if len(distinct_nodes) == 0:
                raise ValueError(
                    f"Step '{step_name}': 'nodes' must contain at least one node different from 'context_node' ({context_node})"
                )

        # Validate params is JSON string if provided
        if "params" in self.config and not isinstance(self.config["params"], str):
            raise ValueError(f"Step '{step_name}': 'params' must be a JSON string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables:
        - contextId: Context ID from the created context
        - memberPublicKey: Public key of the context member (from context creation)
        """
        return [
            (
                "contextId",
                "context_id_{node_name}",
                "Context ID - ID for the created context",
            ),
            (
                "memberPublicKey",
                "context_member_public_key_{node_name}",
                "Public key of the context member",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        context_node = self.config["context_node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )
        nodes = self.config["nodes"]
        capability = self.config.get("capability", "member")

        console.print(
            f"[bold cyan]Creating mesh: context on {context_node}, connecting {len(nodes)} nodes[/bold cyan]"
        )

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  CreateMesh step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                manager = DockerManager()
        except Exception as e:
            console.print(f"[red]Failed to get manager: {str(e)}[/red]")
            return False

        console.print(f"\n[bold]Step 1: Creating context on {context_node}[/bold]")
        context_rpc_url = get_node_rpc_url(context_node, manager)

        params_json: str | None = None
        if "params" in self.config:
            try:
                params_json = self.config["params"]
                json_lib.loads(params_json)
                console.print("[blue]Using initialization params JSON[/blue]")
            except json_lib.JSONDecodeError as e:
                console.print(f"[red]Failed to parse params JSON: {str(e)}[/red]")
                return False

        try:
            client = get_client_for_rpc_url(context_rpc_url)
            protocol = self.config.get("protocol", DEFAULT_PROTOCOL)
            api_result = client.create_context(
                application_id=application_id,
                protocol=protocol,
                params=params_json,
            )
            context_result = ok(api_result)
        except Exception as e:
            context_result = fail("create_context failed", error=e)

        if not context_result.get("success"):
            console.print(
                f"[red]Context creation failed: {context_result.get('error', 'Unknown error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(context_result["data"]):
            return False

        context_data = context_result["data"]

        console.print("[cyan]🔍 Context Creation API Response:[/cyan]")
        console.print(f"  Success: {context_result.get('success')}")
        if isinstance(context_data, dict):
            try:
                formatted_data = json_lib.dumps(context_data, indent=2)
                console.print(f"  Data:\n{formatted_data}")
            except Exception:
                console.print(f"  Data: {context_data}")
        else:
            console.print(f"  Data: {context_data} (type: {type(context_data)})")

        if not isinstance(context_data, dict):
            console.print(
                f"[red]Unexpected context data format: {type(context_data)}[/red]"
            )
            return False

        context_id = extract_nested_data(context_data, "contextId", "id", "name")
        member_public_key = extract_nested_data(context_data, "memberPublicKey")

        if not context_id:
            console.print("[red]Failed to extract context ID from response[/red]")
            if isinstance(context_data, dict):
                console.print(
                    f"[yellow]Available keys in response: {list(context_data.keys())}[/yellow]"
                )
                try:
                    console.print(
                        f"[yellow]Full response structure:\n{json_lib.dumps(context_data, indent=2)}[/yellow]"
                    )
                except Exception:
                    pass
            return False

        if not member_public_key:
            console.print(
                "[red]Failed to extract member public key from context creation response[/red]"
            )
            console.print(
                "[red]Member public key is required to invite other nodes to the context[/red]"
            )
            if isinstance(context_data, dict):
                console.print(
                    f"[yellow]Available keys in response: {list(context_data.keys())}[/yellow]"
                )
                try:
                    console.print(
                        f"[yellow]Full response structure:\n{json_lib.dumps(context_data, indent=2)}[/yellow]"
                    )
                except Exception:
                    pass
            return False

        console.print(f"[green]✓ Context created: {context_id}[/green]")
        if member_public_key:
            console.print(f"[green]✓ Member public key: {member_public_key}[/green]")

        step_key = f"context_{context_node}"
        workflow_results[step_key] = context_data
        self._export_variables(context_data, context_node, dynamic_values)

        if "outputs" not in self.config:
            if "context_id" not in dynamic_values:
                dynamic_values["context_id"] = context_id
            if member_public_key and "member_public_key" not in dynamic_values:
                dynamic_values["member_public_key"] = member_public_key

        nodes_to_process_count = len([n for n in nodes if n != context_node])
        connected_nodes = [context_node]

        for node_name in nodes:
            if node_name == context_node:
                console.print(
                    f"[yellow]⚠️  Skipping {node_name} (same as context node)[/yellow]"
                )
                continue

            console.print(f"\n[bold]Processing node: {node_name}[/bold]")

            console.print(f"  [cyan]Creating identity on {node_name}...[/cyan]")
            node_rpc_url = get_node_rpc_url(node_name, manager)
            identity_result = await generate_identity_via_admin_api(node_rpc_url)

            if not identity_result.get("success"):
                console.print(
                    f"[red]Identity creation failed for {node_name}: {identity_result.get('error', 'Unknown error')}[/red]"
                )
                return False

            if self._check_jsonrpc_error(identity_result["data"]):
                return False

            identity_data_raw = identity_result["data"]
            console.print(
                f"[cyan]🔍 Identity Creation API Response for {node_name}:[/cyan]"
            )
            console.print(f"  Success: {identity_result.get('success')}")
            if isinstance(identity_data_raw, dict):
                try:
                    formatted_data = json_lib.dumps(identity_data_raw, indent=2)
                    console.print(f"  Data:\n{formatted_data}")
                except Exception:
                    console.print(f"  Data: {identity_data_raw}")
            else:
                console.print(
                    f"  Data: {identity_data_raw} (type: {type(identity_data_raw)})"
                )

            if not isinstance(identity_data_raw, dict):
                console.print(
                    f"[red]Unexpected identity data format: {type(identity_data_raw)}[/red]"
                )
                return False

            public_key = extract_nested_data(
                identity_data_raw, "publicKey", "id", "name"
            )

            if not public_key:
                console.print(
                    f"[red]Failed to extract public key from {node_name}[/red]"
                )
                console.print(
                    f"[yellow]Available keys in response: {list(identity_data_raw.keys())}[/yellow]"
                )
                try:
                    console.print(
                        f"[yellow]Full response structure:\n{json_lib.dumps(identity_data_raw, indent=2)}[/yellow]"
                    )
                except Exception:
                    pass
                return False

            console.print(f"  [green]✓ Identity created: {public_key}[/green]")

            identity_key = f"identity_{node_name}"
            actual_identity_data = (
                identity_data_raw.get("data", identity_data_raw)
                if isinstance(identity_data_raw, dict) and "data" in identity_data_raw
                else identity_data_raw
            )
            workflow_results[identity_key] = actual_identity_data

            if f"public_key_{node_name}" not in dynamic_values:
                dynamic_values[f"public_key_{node_name}"] = public_key
            if nodes_to_process_count == 1 and "public_key" not in dynamic_values:
                dynamic_values["public_key"] = public_key

            console.print(
                f"  [cyan]Inviting {public_key} from {context_node}...[/cyan]"
            )
            invite_result = await invite_identity_via_admin_api(
                context_rpc_url, context_id, member_public_key, public_key, capability
            )

            if not invite_result.get("success"):
                console.print(
                    f"[red]Invitation failed for {node_name}: {invite_result.get('error', 'Unknown error')}[/red]"
                )
                return False

            if self._check_jsonrpc_error(invite_result["data"]):
                return False

            invite_data = invite_result["data"]
            invitation = (
                invite_data.get("data")
                if isinstance(invite_data, dict)
                else invite_data
            )

            if not invitation:
                console.print(
                    f"[red]Failed to extract invitation for {node_name}[/red]"
                )
                return False

            console.print("  [green]✓ Invitation created[/green]")

            invite_key = f"invite_{context_node}_{public_key}"
            workflow_results[invite_key] = invitation

            console.print(f"  [cyan]Joining context from {node_name}...[/cyan]")
            join_result = await join_context_via_admin_api(
                node_rpc_url, context_id, public_key, invitation
            )

            if not join_result.get("success"):
                console.print(
                    f"[red]Join failed for {node_name}: {join_result.get('error', 'Unknown error')}[/red]"
                )
                return False

            if self._check_jsonrpc_error(join_result["data"]):
                return False

            console.print("  [green]✓ Joined context successfully[/green]")

            join_key = f"join_{node_name}_{public_key}"
            workflow_results[join_key] = join_result["data"]
            connected_nodes.append(node_name)

        console.print("\n[bold green]✓ Mesh created successfully![/bold green]")
        console.print(f"  Context: {context_id} on {context_node}")
        console.print(f"  Connected nodes: {', '.join(connected_nodes)}")

        return True
