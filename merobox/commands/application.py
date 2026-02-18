"""
Application command - List applications on Calimero nodes.
"""

import json as json_lib
import sys

import click
import requests
from rich import box
from rich.table import Table

from merobox.commands.constants import (
    ADMIN_API_APPLICATIONS,
    DEFAULT_CONNECTION_TIMEOUT,
)
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import (
    console,
    get_node_rpc_url,
    run_async_function,
)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def list_applications_via_admin_api(rpc_url: str) -> dict:
    """List applications using direct HTTP call."""
    try:
        response = requests.get(
            f"{rpc_url}{ADMIN_API_APPLICATIONS}", timeout=DEFAULT_CONNECTION_TIMEOUT
        )
        response.raise_for_status()
        return ok(response.json())
    except Exception as e:
        return fail("list_applications failed", error=e)


def create_application_table(applications_data: list) -> Table:
    """Create a table to display applications."""
    table = Table(title="Applications", box=box.ROUNDED)
    table.add_column("Application ID", style="cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Type", style="blue")
    table.add_column("Size", style="green")

    for app_info in applications_data:
        if isinstance(app_info, dict):
            # Handle both "id" and "applicationId" fields
            app_id = app_info.get("id") or app_info.get("applicationId", "Unknown")
            source = app_info.get("source", "N/A")
            # Determine type from source
            if source and source.startswith("file://"):
                app_type = "dev"
            else:
                app_type = "remote"
            size = app_info.get("size", "N/A")
            if isinstance(size, int):
                size_kb = size / 1024
                if size_kb < 1024:
                    size = f"{size_kb:.2f} KB"
                else:
                    size_mb = size_kb / 1024
                    size = f"{size_mb:.2f} MB"
            table.add_row(
                app_id,
                source,
                app_type,
                str(size),
            )
        else:
            # Handle case where app_info is a string (just the ID)
            table.add_row(str(app_info), "N/A", "N/A", "N/A")

    return table


@click.group()
def application():
    """Manage applications on Calimero nodes."""
    pass


@application.command(name="list")
@click.option("--node", "-n", required=True, help="Node name to list applications from")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_applications(node, verbose):
    """List all applications installed on a node."""
    manager = DockerManager()

    # Get admin API URL
    rpc_url = get_node_rpc_url(node, manager)

    console.print(f"[blue]Listing applications on node {node}[/blue]")

    result = run_async_function(list_applications_via_admin_api, rpc_url)

    if result["success"]:
        response_data = result.get("data", {})

        if isinstance(response_data, dict):
            if "data" in response_data:
                response_data = response_data["data"]

            if isinstance(response_data, dict) and "apps" in response_data:
                apps_value = response_data["apps"]
                if isinstance(apps_value, list):
                    applications_data = apps_value
                elif isinstance(apps_value, dict):
                    applications_data = []
                    for key, value in apps_value.items():
                        if isinstance(value, dict):
                            app_obj = {**value}
                            if "id" not in app_obj and "applicationId" not in app_obj:
                                app_obj["id"] = key
                            applications_data.append(app_obj)
                        else:
                            applications_data.append({"id": key, "data": value})
                else:
                    applications_data = []
            elif isinstance(response_data, list):
                applications_data = response_data
            else:
                applications_data = []
        elif isinstance(response_data, list):
            applications_data = response_data
        else:
            applications_data = []

        if not isinstance(applications_data, list):
            console.print(
                f"[yellow]⚠️  Unexpected response format. Expected list, got {type(applications_data)}[/yellow]"
            )
            if verbose:
                try:
                    formatted_data = json_lib.dumps(result, indent=2)
                    console.print(f"[dim]Full response:\n{formatted_data}[/dim]")
                except Exception:
                    console.print(f"[dim]Full response: {result}[/dim]")
            applications_data = []

        if not applications_data:
            console.print(f"\n[yellow]No applications found on node {node}[/yellow]")
            if verbose:
                console.print("\n[bold]Response structure:[/bold]")
                try:
                    formatted_data = json_lib.dumps(result, indent=2)
                    console.print(f"{formatted_data}")
                except Exception:
                    console.print(f"{result}")
            return

        if verbose:
            console.print(
                f"\n[cyan]Extracted {len(applications_data)} application(s) from response[/cyan]"
            )
            try:
                formatted_data = json_lib.dumps(applications_data, indent=2)
                console.print(f"[dim]Applications data:\n{formatted_data}[/dim]")
            except Exception:
                console.print(f"[dim]Applications data: {applications_data}[/dim]")

        console.print(
            f"\n[green]Found {len(applications_data)} application(s):[/green]"
        )

        table = create_application_table(applications_data)
        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            try:
                formatted_data = json_lib.dumps(result, indent=2)
                console.print(f"{formatted_data}")
            except Exception:
                console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to list applications[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)
