"""
Proposals command - Manage Calimero context proposals via admin API.
"""

import sys
from typing import Optional

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import (
    ADMIN_API_CONTEXTS,
)
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


@with_retry(config=NETWORK_RETRY_CONFIG)
async def get_proposal_via_admin_api(
    rpc_url: str, context_id: str, proposal_id: str
) -> dict:
    """Get a specific proposal using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        result = client.get_proposal(context_id=context_id, proposal_id=proposal_id)
        return ok(
            result,
            endpoint=f"{rpc_url}{ADMIN_API_CONTEXTS}/{context_id}/proposals/{proposal_id}",
        )
    except Exception as e:
        return fail("get_proposal failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def list_proposals_via_admin_api(
    rpc_url: str, context_id: str, args: Optional[str] = None
) -> dict:
    """Get proposals list using calimero-client-py."""
    try:
        # If args is None or empty, use default pagination
        if not args or args == "{}":
            args_to_pass = '{"offset": 0, "limit": 100}'
        else:
            args_to_pass = args

        console.print(
            f"[cyan]🔍 Calling list_proposals with context_id: {context_id}, args: {args_to_pass}[/cyan]"
        )
        client = get_client_for_rpc_url(rpc_url)
        result = client.list_proposals(context_id=context_id, args=args_to_pass)
        console.print(f"[green]✓ list_proposals returned: {result}[/green]")
        return ok(
            result, endpoint=f"{rpc_url}{ADMIN_API_CONTEXTS}/{context_id}/proposals"
        )
    except Exception as e:
        console.print(
            f"[red]✗ list_proposals exception: {type(e).__name__}: {str(e)}[/red]"
        )
        return fail("list_proposals failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def get_proposal_approvers_via_admin_api(
    rpc_url: str, context_id: str, proposal_id: str
) -> dict:
    """Get list of approvers for a proposal using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        result = client.get_proposal_approvers(
            context_id=context_id, proposal_id=proposal_id
        )
        return ok(
            result,
            endpoint=f"{rpc_url}{ADMIN_API_CONTEXTS}/{context_id}/proposals/{proposal_id}/approvers",
        )
    except Exception as e:
        return fail("get_proposal_approvers failed", error=e)


@click.group()
def proposals():
    """Manage Calimero context proposals."""
    pass


@proposals.command()
@click.option("--node", "-n", required=True, help="Node name to query")
@click.option("--context-id", required=True, help="Context ID")
@click.option("--proposal-id", required=True, help="Proposal ID")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def get(node, context_id, proposal_id, verbose):
    """Get a specific proposal."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)

    console.print(
        f"[blue]Getting proposal {proposal_id} from context {context_id} on node {node}[/blue]"
    )

    result = run_async_function(
        get_proposal_via_admin_api, rpc_url, context_id, proposal_id
    )

    if result["success"]:
        console.print("\n[green]✓ Proposal retrieved successfully![/green]")

        # Create table
        table = Table(title="Proposal Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        proposal_data = result.get("data", {})
        table.add_row("Proposal ID", proposal_id)
        table.add_row("Context ID", context_id)

        # Add any additional proposal fields
        for key, value in proposal_data.items():
            if key not in ["proposalId", "contextId"]:
                table.add_row(key, str(value))

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")
    else:
        console.print("\n[red]✗ Failed to get proposal[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@proposals.command()
@click.option("--node", "-n", required=True, help="Node name to query")
@click.option("--context-id", required=True, help="Context ID")
@click.option("--args", default=None, help="Optional arguments for filtering proposals")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list(node, context_id, args, verbose):
    """List proposals for a context."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)

    console.print(
        f"[blue]Listing proposals for context {context_id} on node {node}[/blue]"
    )

    result = run_async_function(list_proposals_via_admin_api, rpc_url, context_id, args)

    if result["success"]:
        proposals_data = result.get("data", [])
        if isinstance(proposals_data, list):
            count = len(proposals_data)
        else:
            count = 1 if proposals_data else 0
            proposals_data = [proposals_data] if proposals_data else []

        console.print(f"\n[green]✓ Found {count} proposals![/green]")

        if proposals_data:
            # Create table
            table = Table(title=f"Proposals for Context {context_id}", box=box.ROUNDED)
            table.add_column("Proposal ID", style="cyan")
            table.add_column("Status", style="yellow")
            table.add_column("Details", style="green")

            for proposal in proposals_data:
                if isinstance(proposal, dict):
                    proposal_id = proposal.get(
                        "id", proposal.get("proposalId", "Unknown")
                    )
                    status = proposal.get("status", "Unknown")
                    details = str(
                        {
                            k: v
                            for k, v in proposal.items()
                            if k not in ["id", "proposalId", "status"]
                        }
                    )
                else:
                    proposal_id = str(proposal)
                    status = "Unknown"
                    details = ""
                table.add_row(proposal_id, status, details)

            console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")
    else:
        console.print("\n[red]✗ Failed to list proposals[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@proposals.command()
@click.option("--node", "-n", required=True, help="Node name to query")
@click.option("--context-id", required=True, help="Context ID")
@click.option("--proposal-id", required=True, help="Proposal ID")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def approvers(node, context_id, proposal_id, verbose):
    """Get list of approvers for a proposal."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)

    console.print(
        f"[blue]Getting approvers for proposal {proposal_id} in context {context_id}[/blue]"
    )

    result = run_async_function(
        get_proposal_approvers_via_admin_api, rpc_url, context_id, proposal_id
    )

    if result["success"]:
        approvers_data = result.get("data", [])
        if isinstance(approvers_data, list):
            count = len(approvers_data)
        else:
            count = 1 if approvers_data else 0
            approvers_data = [approvers_data] if approvers_data else []

        console.print(f"\n[green]✓ Found {count} approvers![/green]")

        if approvers_data:
            # Create table
            table = Table(
                title=f"Approvers for Proposal {proposal_id}", box=box.ROUNDED
            )
            table.add_column("Approver ID", style="cyan")
            table.add_column("Details", style="green")

            for approver in approvers_data:
                if isinstance(approver, dict):
                    approver_id = approver.get("id", str(approver))
                    details = str({k: v for k, v in approver.items() if k != "id"})
                else:
                    approver_id = str(approver)
                    details = "N/A"
                table.add_row(approver_id, details)

            console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")
    else:
        console.print("\n[red]✗ Failed to get approvers[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    proposals()
