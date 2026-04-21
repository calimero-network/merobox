"""
Create namespace step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class CreateNamespaceStep(BaseStep):
    """Execute a create namespace step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("application_id"), str):
            raise ValueError(f"Step '{step_name}': 'application_id' must be a string")
        if "alias" in self.config and not isinstance(self.config.get("alias"), str):
            raise ValueError(f"Step '{step_name}': 'alias' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "namespaceId",
                "namespace_id_{node_name}",
                "Namespace ID - primary identifier for the created namespace",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )

        alias = None
        if "alias" in self.config:
            alias = self._resolve_dynamic_value(
                self.config["alias"], workflow_results, dynamic_values
            )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            create_namespace = getattr(client, "create_namespace", None)
            if callable(create_namespace):
                api_result = create_namespace(
                    application_id=application_id,
                    alias=alias,
                )
            else:
                # Backward compatibility for older client versions.
                api_result = client.create_group(application_id=application_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("create_namespace failed", error=e)

        expected_failure = self._is_expected_failure()

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                if expected_failure:
                    self._report_expected_failure("JSON-RPC error returned")
                    return True
                return False

            namespace_data = result["data"]
            if isinstance(namespace_data, dict) and "data" in namespace_data:
                namespace_data = namespace_data["data"]

            step_key = f"namespace_{node_name}"
            workflow_results[step_key] = namespace_data
            self._export_variables(namespace_data, node_name, dynamic_values)

            # Fallback: ensure namespace_id is captured
            if f"namespace_id_{node_name}" not in dynamic_values:
                if isinstance(namespace_data, dict):
                    namespace_id = namespace_data.get("namespaceId")
                    if namespace_id:
                        dynamic_values[f"namespace_id_{node_name}"] = namespace_id
                        console.print(
                            f"[blue]Captured namespace ID for {node_name}: {namespace_id}[/blue]"
                        )

            console.print(
                f"[green]✓ Namespace created on {node_name}: "
                f"{dynamic_values.get(f'namespace_id_{node_name}', 'unknown')}[/green]"
            )
            if expected_failure:
                self._report_unexpected_success()
            return True
        else:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]Namespace creation failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False


# Deprecated alias kept for backward compatibility.
CreateGroupStep = CreateNamespaceStep
