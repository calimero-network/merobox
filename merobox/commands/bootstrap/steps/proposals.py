"""
Proposals workflow step executors

Supported steps:
- get_proposal: Get a specific proposal
- list_proposals: List proposals in a context
- get_proposal_approvers: Get list of approvers for a proposal
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.proposals import (
    approve_proposal_via_admin_api,
    create_and_approve_proposal_via_admin_api,
    get_proposal_approvers_via_admin_api,
    get_proposal_via_admin_api,
    list_proposals_via_admin_api,
)
from merobox.commands.utils import console, get_node_rpc_url


class GetProposalStep(BaseStep):
    """Get a specific proposal."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "proposal_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        if not isinstance(self.config.get("proposal_id"), str):
            raise ValueError(f"Step '{step_name}': 'proposal_id' must be a string")

    def _get_exportable_variables(self):
        return [
            ("proposal", "proposal_{node_name}", "Proposal data"),
            ("proposalId", "proposal_id_{node_name}", "Proposal ID"),
            ("status", "proposal_status_{node_name}", "Proposal status"),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        proposal_id = self._resolve_dynamic_value(
            self.config["proposal_id"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Get proposal step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        result = await get_proposal_via_admin_api(rpc_url, context_id, proposal_id)

        console.print(f"[cyan]🔍 Get Proposal API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")

        if result["success"]:
            response_data = result["data"]
            if isinstance(response_data, dict) and "data" in response_data:
                actual_proposal = response_data["data"]
            else:
                actual_proposal = response_data

            if self._check_jsonrpc_error(actual_proposal):
                return False

            step_key = f"proposal_{node_name}"
            workflow_results[step_key] = actual_proposal

            self._export_variables(actual_proposal, node_name, dynamic_values)

            console.print(
                f"[green]✓ Proposal retrieved successfully on {node_name}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]✗ Failed to get proposal on {node_name}: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


class ListProposalsStep(BaseStep):
    """List proposals for a context."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        if "args" in self.config and not isinstance(self.config.get("args"), str):
            raise ValueError(f"Step '{step_name}': 'args' must be a string")

    def _get_exportable_variables(self):
        return [
            ("proposals", "proposals_list_{node_name}", "List of proposals"),
            ("data", "proposals_data_{node_name}", "Raw proposals data"),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        args = self.config.get("args", None)
        if args:
            args = self._resolve_dynamic_value(args, workflow_results, dynamic_values)

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  List proposals step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        result = await list_proposals_via_admin_api(rpc_url, context_id, args)

        console.print(f"[cyan]🔍 List Proposals API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")

        data = result.get("data", [])
        if isinstance(data, dict) and "data" in data:
            proposals_list = data["data"]
            if isinstance(proposals_list, list):
                console.print(f"  Proposals count: {len(proposals_list)}")
            else:
                console.print(f"  Proposals data type: {type(proposals_list)}")
        else:
            console.print(f"  Data type: {type(data)}")

        if result["success"]:
            response_data = result["data"]

            if isinstance(response_data, dict) and "data" in response_data:
                actual_proposals = response_data["data"]
            else:
                actual_proposals = response_data

            if self._check_jsonrpc_error(actual_proposals):
                return False

            step_key = f"proposals_{node_name}"
            workflow_results[step_key] = actual_proposals

            self._export_variables(actual_proposals, node_name, dynamic_values)

            console.print(
                f"[green]✓ Proposals retrieved successfully on {node_name}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]✗ Failed to list proposals on {node_name}: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


class GetProposalApproversStep(BaseStep):
    """Get list of approvers for a proposal."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "proposal_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        if not isinstance(self.config.get("proposal_id"), str):
            raise ValueError(f"Step '{step_name}': 'proposal_id' must be a string")

    def _get_exportable_variables(self):
        return [
            ("approvers", "proposal_approvers_{node_name}", "List of approvers"),
            ("data", "approvers_data_{node_name}", "Raw approvers data"),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        proposal_id = self._resolve_dynamic_value(
            self.config["proposal_id"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Get proposal approvers step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        result = await get_proposal_approvers_via_admin_api(
            rpc_url, context_id, proposal_id
        )

        console.print(
            f"[cyan]🔍 Get Proposal Approvers API Response for {node_name}:[/cyan]"
        )
        console.print(f"  Success: {result.get('success')}")

        data = result.get("data", [])
        if isinstance(data, dict) and "data" in data:
            approvers_list = data["data"]
            if isinstance(approvers_list, list):
                console.print(f"  Approvers count: {len(approvers_list)}")
            else:
                console.print(f"  Approvers data type: {type(approvers_list)}")
        elif isinstance(data, list):
            console.print(f"  Approvers count: {len(data)}")
        else:
            console.print(f"  Data type: {type(data)}")

        if result["success"]:
            response_data = result["data"]

            if isinstance(response_data, dict) and "data" in response_data:
                actual_approvers = response_data["data"]
            else:
                actual_approvers = response_data

            if self._check_jsonrpc_error(actual_approvers):
                return False

            step_key = f"proposal_approvers_{node_name}"
            workflow_results[step_key] = actual_approvers

            self._export_variables(actual_approvers, node_name, dynamic_values)

            console.print(
                f"[green]✓ Proposal approvers retrieved successfully on {node_name}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]✗ Failed to get proposal approvers on {node_name}: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


class CreateAndApproveProposalStep(BaseStep):
    """Create and approve a proposal."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "request_json"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        if not isinstance(self.config.get("request_json"), str):
            raise ValueError(f"Step '{step_name}': 'request_json' must be a string")

    def _get_exportable_variables(self):
        return [
            ("proposal", "created_proposal_{node_name}", "Created proposal data"),
            ("proposalId", "created_proposal_id_{node_name}", "Created proposal ID"),
            (
                "status",
                "created_proposal_status_{node_name}",
                "Created proposal status",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        request_json = self._resolve_dynamic_value(
            self.config["request_json"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Create and approve proposal step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        result = await create_and_approve_proposal_via_admin_api(
            rpc_url, context_id, request_json
        )

        console.print(
            f"[cyan]🔍 Create and Approve Proposal API Response for {node_name}:[/cyan]"
        )
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")

        if result["success"]:
            response_data = result["data"]

            if isinstance(response_data, dict) and "data" in response_data:
                actual_proposal = response_data["data"]
            else:
                actual_proposal = response_data

            if self._check_jsonrpc_error(actual_proposal):
                return False

            step_key = f"create_and_approve_proposal_{node_name}"
            workflow_results[step_key] = actual_proposal

            self._export_variables(actual_proposal, node_name, dynamic_values)

            console.print(
                f"[green]✓ Proposal created and approved successfully on {node_name}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]✗ Failed to create and approve proposal on {node_name}: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


class ApproveProposalStep(BaseStep):
    """Approve a proposal."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "request_json"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        if not isinstance(self.config.get("request_json"), str):
            raise ValueError(f"Step '{step_name}': 'request_json' must be a string")

    def _get_exportable_variables(self):
        return [
            ("proposal", "approved_proposal_{node_name}", "Approved proposal data"),
            ("proposalId", "approved_proposal_id_{node_name}", "Approved proposal ID"),
            (
                "status",
                "approved_proposal_status_{node_name}",
                "Approved proposal status",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        request_json = self._resolve_dynamic_value(
            self.config["request_json"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Approve proposal step export configuration validation failed[/yellow]"
            )

        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        result = await approve_proposal_via_admin_api(rpc_url, context_id, request_json)

        console.print(f"[cyan]🔍 Approve Proposal API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")

        if result["success"]:
            response_data = result["data"]

            if isinstance(response_data, dict) and "data" in response_data:
                actual_proposal = response_data["data"]
            else:
                actual_proposal = response_data

            if self._check_jsonrpc_error(actual_proposal):
                return False

            step_key = f"approve_proposal_{node_name}"
            workflow_results[step_key] = actual_proposal

            self._export_variables(actual_proposal, node_name, dynamic_values)

            console.print(
                f"[green]✓ Proposal approved successfully on {node_name}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]✗ Failed to approve proposal on {node_name}: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
