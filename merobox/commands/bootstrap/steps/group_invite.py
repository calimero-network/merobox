"""
Create namespace invitation step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class CreateNamespaceInvitationStep(BaseStep):
    """Execute a create namespace invitation step."""

    def _get_required_fields(self) -> list[str]:
        # Support deprecated alias 'group_id' via custom validation below.
        return ["node"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        namespace_id = self.config.get("namespace_id", self.config.get("group_id"))
        if not isinstance(namespace_id, str):
            raise ValueError(
                f"Step '{step_name}': 'namespace_id' (or deprecated 'group_id') must be a string"
            )

    def _get_exportable_variables(self):
        # The invitation object is stored as JSON string for use in join_namespace steps
        return [
            (
                "invitation",
                "namespace_invitation_{node_name}",
                "Invitation object (JSON) for joining the namespace",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config.get("namespace_id", self.config.get("group_id")),
            workflow_results,
            dynamic_values,
        )
        recursive = bool(self.config.get("recursive", False))

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            create_namespace_invitation = getattr(
                client, "create_namespace_invitation", None
            )
            if callable(create_namespace_invitation):
                api_result = create_namespace_invitation(
                    namespace_id=namespace_id,
                    recursive=recursive,
                )
            else:
                # Backward compatibility for older client versions.
                api_result = client.create_group_invitation(namespace_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("create_namespace_invitation failed", error=e)

        expected_failure = self._is_expected_failure()

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                if expected_failure:
                    self._report_expected_failure("JSON-RPC error returned")
                    return True
                return False

            step_name = self.config.get("name", "")
            step_key = f"namespace_invitation_{node_name}"
            if step_name:
                step_key = f"namespace_invitation_{step_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            raw_data = result["data"]
            if isinstance(raw_data, dict):
                nested = raw_data.get("data", raw_data)
                if isinstance(nested, dict):
                    # Only write the auto-keyed fallback when no custom outputs
                    # are configured; otherwise the custom outputs handle export
                    # and the shared key would collide when the same node creates
                    # multiple invitations.
                    if "outputs" not in self.config:
                        dynamic_values[f"namespace_invitation_{node_name}"] = nested
                    console.print(
                        f"[green]✓ Namespace invitation created on {node_name}[/green]"
                    )
                    if expected_failure:
                        self._report_unexpected_success()
                    return True

            console.print(
                f"[yellow]⚠️  Could not extract invitation from response on {node_name}[/yellow]"
            )
            return False
        else:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]Namespace invitation creation failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False


# Deprecated alias kept for backward compatibility.
CreateGroupInvitationStep = CreateNamespaceInvitationStep
