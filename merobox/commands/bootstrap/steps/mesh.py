"""
Create mesh step executor - Creates a context and connects multiple nodes to it.

The mesh step uses the namespace governance flow:
1. Create namespace on the context_node
2. Create context in that namespace
3. Pre-install the application on joining nodes (if path is provided)
4. For each joining node: create namespace invitation, join namespace
5. Contexts are joined automatically via namespace/group auto_join
"""

import json as json_lib
import os
import shutil
from typing import Any, Optional

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import CONTAINER_DATA_DIR_PATTERNS, DEFAULT_METADATA
from merobox.commands.identity import (
    create_namespace_invitation_via_admin_api,
    generate_identity_via_admin_api,
)
from merobox.commands.join import join_namespace_via_admin_api
from merobox.commands.result import fail, ok
from merobox.commands.utils import console, extract_nested_data


class CreateMeshStep(BaseStep):
    """Execute a create mesh step that creates a context and connects multiple nodes."""

    def _get_required_fields(self) -> list[str]:
        return ["context_node", "application_id", "nodes"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("context_node"), str):
            raise ValueError(f"Step '{step_name}': 'context_node' must be a string")

        if not isinstance(self.config.get("application_id"), str):
            raise ValueError(f"Step '{step_name}': 'application_id' must be a string")

        nodes = self.config.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError(f"Step '{step_name}': 'nodes' must be a list")
        if len(nodes) == 0:
            raise ValueError(f"Step '{step_name}': 'nodes' must not be empty")

        for i, node in enumerate(nodes):
            if not isinstance(node, str):
                raise ValueError(f"Step '{step_name}': 'nodes[{i}]' must be a string")

        context_node = self.config.get("context_node")
        if context_node and isinstance(context_node, str):
            distinct_nodes = [n for n in nodes if n != context_node]
            if len(distinct_nodes) == 0:
                raise ValueError(
                    f"Step '{step_name}': 'nodes' must contain at least one node different from 'context_node' ({context_node})"
                )

        if "path" in self.config and not isinstance(self.config["path"], str):
            raise ValueError(f"Step '{step_name}': 'path' must be a string")

        if "params" in self.config and not isinstance(self.config["params"], str):
            raise ValueError(f"Step '{step_name}': 'params' must be a JSON string")

    def _get_exportable_variables(self):
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

    def _is_binary_mode(self) -> bool:
        manager = getattr(self, "manager", None)
        if manager is None:
            return False
        return hasattr(manager, "binary_path") and manager.binary_path is not None

    def _resolve_application_path(self, path: str) -> str:
        expanded_path = os.path.expanduser(path)
        return os.path.abspath(expanded_path)

    def _prepare_container_path(
        self, node_name: str, source_path: str
    ) -> Optional[str]:
        container_data_dir: Optional[str] = None

        for pattern in CONTAINER_DATA_DIR_PATTERNS:
            if "{node_name}" in pattern:
                candidate = pattern.format(node_name=node_name)
            else:
                candidate = None

            if candidate and os.path.exists(candidate):
                container_data_dir = candidate
                break

        if not container_data_dir or not os.path.exists(container_data_dir):
            console.print(
                f"[red]Container data directory not found for {node_name}[/red]"
            )
            return None
        try:
            abs_container_data_dir = os.path.abspath(container_data_dir)
            abs_source_path = os.path.abspath(source_path)
            if (
                os.path.commonpath([abs_source_path, abs_container_data_dir])
                == abs_container_data_dir
            ):
                filename = os.path.basename(source_path)
                return f"/app/data/{filename}"
        except ValueError:
            pass

        filename = os.path.basename(source_path)
        try:
            os.makedirs(container_data_dir, exist_ok=True)
            container_file_path = os.path.join(container_data_dir, filename)
            shutil.copy2(source_path, container_file_path)
            console.print(
                f"[blue]Copied file to container data directory: {container_file_path}[/blue]"
            )
            return f"/app/data/{filename}"
        except (OSError, shutil.Error) as error:
            console.print(
                f"[red]Failed to copy file to container data directory: {error}[/red]"
            )
            return None

    def _install_application_on_node(
        self, node_name: str, application_path: str
    ) -> bool:
        """Install the application WASM on a single node.

        Returns True on success, False on failure.
        """
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)

            if self._is_binary_mode():
                api_result = client.install_dev_application(
                    path=application_path, metadata=DEFAULT_METADATA
                )
            else:
                container_path = self._prepare_container_path(
                    node_name, application_path
                )
                if not container_path:
                    console.print(
                        f"[red]Unable to prepare application file for {node_name}[/red]"
                    )
                    return False
                api_result = client.install_dev_application(
                    path=container_path, metadata=DEFAULT_METADATA
                )

            if isinstance(api_result, dict) and "error" in api_result:
                console.print(
                    f"[yellow]Warning: install_dev_application returned error for {node_name}: {api_result['error']}[/yellow]"
                )
                return False

            console.print(
                f"  [green]\u2713 Pre-installed application on {node_name}[/green]"
            )
            return True
        except Exception as e:
            console.print(
                f"  [yellow]\u26a0\ufe0f Failed to pre-install app on {node_name}: {e}[/yellow]"
            )
            return False

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        context_node = self.config["context_node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )
        nodes = self.config["nodes"]

        console.print(
            f"[bold cyan]Creating mesh: context on {context_node}, connecting {len(nodes)} nodes[/bold cyan]"
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  CreateMesh step export configuration validation failed[/yellow]"
            )

        console.print(f"\n[bold]Step 1: Creating namespace on {context_node}[/bold]")
        try:
            context_rpc_url, client_context_node = self._resolve_node_for_client(
                context_node
            )
        except Exception as e:
            console.print(
                f"[red]Failed to resolve context node {context_node}: {str(e)}[/red]"
            )
            return False

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
            client = get_client_for_rpc_url(
                context_rpc_url, node_name=client_context_node
            )
            create_namespace = getattr(client, "create_namespace", None)
            if callable(create_namespace):
                namespace_result = ok(create_namespace(application_id=application_id))
            else:
                # Backward compatibility for older client versions.
                namespace_result = ok(
                    client.create_group(
                        application_id=application_id,
                        parent_group_id=None,
                    )
                )
        except Exception as e:
            namespace_result = fail("create_namespace failed", error=e)

        if not namespace_result.get("success"):
            console.print(
                f"[red]Namespace creation failed: {namespace_result.get('error', 'Unknown error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(namespace_result["data"]):
            return False

        namespace_data = namespace_result["data"]
        if isinstance(namespace_data, dict) and "data" in namespace_data:
            namespace_data = namespace_data["data"]
        namespace_id = extract_nested_data(namespace_data, "namespaceId", "groupId")
        if not namespace_id:
            console.print("[red]Failed to extract namespace ID from response[/red]")
            return False

        console.print(f"[green]✓ Namespace created: {namespace_id}[/green]")
        dynamic_values["namespace_id"] = namespace_id

        console.print(f"\n[bold]Step 2: Creating context on {context_node}[/bold]")
        try:
            context_kwargs = {
                "application_id": application_id,
                "params": params_json,
                "group_id": namespace_id,
            }
            api_result = client.create_context(**context_kwargs)
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
            if isinstance(context_data, dict):
                console.print(
                    f"[yellow]Available keys in response: {list(context_data.keys())}[/yellow]"
                )
            return False

        console.print(f"[green]✓ Context created: {context_id}[/green]")
        if member_public_key:
            console.print(f"[green]✓ Member public key: {member_public_key}[/green]")
        console.print(f"[green]✓ Namespace ID: {namespace_id}[/green]")

        step_key = f"context_{context_node}"
        workflow_results[step_key] = context_data
        self._export_variables(context_data, context_node, dynamic_values)

        if "outputs" not in self.config:
            if "context_id" not in dynamic_values:
                dynamic_values["context_id"] = context_id
            if member_public_key and "member_public_key" not in dynamic_values:
                dynamic_values["member_public_key"] = member_public_key
            if "group_id" not in dynamic_values:
                dynamic_values["group_id"] = namespace_id

        connected_nodes = [context_node]

        # Pre-install application on all joining nodes so context state sync works.
        # The core join protocol no longer transfers application blobs.
        raw_application_path = self.config.get("path")
        if raw_application_path:
            application_path = self._resolve_application_path(raw_application_path)
            if not os.path.isfile(application_path):
                console.print(
                    f"[red]Application path not found or not a file: {application_path}[/red]"
                )
                return False

            console.print("\n[bold]Pre-installing application on joining nodes[/bold]")
            for node_name in nodes:
                if node_name == context_node:
                    continue
                if not self._install_application_on_node(node_name, application_path):
                    console.print(
                        f"[red]Failed to pre-install application on {node_name}[/red]"
                    )
                    return False

        for node_name in nodes:
            if node_name == context_node:
                console.print(
                    f"[yellow]⚠️  Skipping {node_name} (same as context node)[/yellow]"
                )
                continue

            console.print(f"\n[bold]Processing node: {node_name}[/bold]")

            console.print(f"  [cyan]Creating identity on {node_name}...[/cyan]")
            try:
                node_rpc_url, client_node_name = self._resolve_node_for_client(
                    node_name
                )
            except Exception as e:
                console.print(
                    f"[red]Failed to resolve node {node_name}: {str(e)}[/red]"
                )
                return False
            identity_result = await generate_identity_via_admin_api(
                node_rpc_url, node_name=client_node_name
            )

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
                return False

            console.print(f"  [green]✓ Identity created: {public_key}[/green]")

            identity_key = f"identity_{node_name}"
            actual_identity_data = (
                identity_data_raw.get("data", identity_data_raw)
                if isinstance(identity_data_raw, dict) and "data" in identity_data_raw
                else identity_data_raw
            )
            workflow_results[identity_key] = actual_identity_data

            nodes_to_process_count = len([n for n in nodes if n != context_node])
            if f"public_key_{node_name}" not in dynamic_values:
                dynamic_values[f"public_key_{node_name}"] = public_key
            if nodes_to_process_count == 1 and "public_key" not in dynamic_values:
                dynamic_values["public_key"] = public_key

            # Step: Create namespace invitation from context node
            console.print(
                f"  [cyan]Creating namespace invitation from {context_node}...[/cyan]"
            )
            invite_result = await create_namespace_invitation_via_admin_api(
                context_rpc_url,
                namespace_id,
                node_name=client_context_node,
            )

            if not invite_result.get("success"):
                console.print(
                    f"[red]Namespace invitation failed for {node_name}: {invite_result.get('error', 'Unknown error')}[/red]"
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

            console.print("  [green]✓ Namespace invitation created[/green]")

            invite_key = f"invite_{context_node}_{node_name}"
            workflow_results[invite_key] = invitation

            # Step: Join namespace from the joining node
            console.print(f"  [cyan]Joining namespace from {node_name}...[/cyan]")
            invitation_json = (
                json_lib.dumps(invitation)
                if isinstance(invitation, dict)
                else str(invitation)
            )
            join_result = await join_namespace_via_admin_api(
                node_rpc_url,
                namespace_id,
                invitation_json,
                node_name=client_node_name,
            )

            if not join_result.get("success"):
                console.print(
                    f"[red]Join namespace failed for {node_name}: {join_result.get('error', 'Unknown error')}[/red]"
                )
                return False

            if self._check_jsonrpc_error(join_result["data"]):
                return False

            console.print("  [green]✓ Joined namespace successfully[/green]")

            join_key = f"join_{node_name}"
            join_data = join_result["data"]
            workflow_results[join_key] = join_data

            join_nested = (
                join_data.get("data", join_data)
                if isinstance(join_data, dict)
                else join_data
            )
            member_identity = (
                join_nested.get("memberIdentity")
                if isinstance(join_nested, dict)
                else None
            )
            if member_identity:
                dynamic_values[f"member_identity_{node_name}"] = member_identity
                dynamic_values[f"public_key_{node_name}"] = member_identity
                if nodes_to_process_count == 1:
                    dynamic_values["memberIdentity"] = member_identity
                console.print(f"  [green]✓ Member identity: {member_identity}[/green]")

            connected_nodes.append(node_name)

        console.print("\n[bold green]✓ Mesh created successfully![/bold green]")
        console.print(f"  Context: {context_id} on {context_node}")
        console.print(f"  Namespace: {namespace_id}")
        console.print(f"  Connected nodes: {', '.join(connected_nodes)}")

        return True
