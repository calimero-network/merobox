"""
Health command - Check Calimero node health using admin API.
"""

import asyncio
import sys

import aiohttp
import click
from rich import box
from rich.table import Table

from merobox.commands.manager import DockerManager
from merobox.commands.utils import (
    check_node_running,
    console,
    extract_nested_data,
    get_node_rpc_url,
    run_async_function,
)


async def check_node_health(rpc_url: str) -> dict:
    """Check the health of a Calimero node using the admin API."""
    try:
        async with aiohttp.ClientSession() as session:
            # Check health endpoint
            async with session.get(
                f"{rpc_url}/admin-api/health", timeout=10
            ) as response:
                if response.status == 200:
                    health_data = await response.json()
                else:
                    health_data = {"error": f"HTTP {response.status}"}

            # Check authentication status
            async with session.get(
                f"{rpc_url}/admin-api/is-authed", timeout=10
            ) as response:
                if response.status == 200:
                    auth_data = await response.json()
                else:
                    auth_data = {"error": f"HTTP {response.status}"}

            # Check peers count
            async with session.get(
                f"{rpc_url}/admin-api/peers", timeout=10
            ) as response:
                if response.status == 200:
                    peers_data = await response.json()
                else:
                    peers_data = {"error": f"HTTP {response.status}"}

            return {
                "health": health_data,
                "authenticated": auth_data,
                "peers": peers_data,
                "success": True,
            }

    except asyncio.TimeoutError:
        return {"error": "Timeout", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


def extract_health_status(health_data):
    """Extract health status from health response."""
    return extract_nested_data(health_data, "status") or "Unknown"


def extract_auth_status(auth_data):
    """Extract authentication status from auth response."""
    return extract_nested_data(auth_data, "authenticated") or "Unknown"


def extract_peers_count(peers_data):
    """Extract peers count from peers response."""
    peers = extract_nested_data(peers_data, "peers", "data")
    if isinstance(peers, list):
        return len(peers)
    elif isinstance(peers, dict) and "peers" in peers:
        return len(peers["peers"])
    return 0


@click.command()
@click.option(
    "--node",
    "-n",
    help="Specific node name to check (default: check all running nodes)",
)
@click.option(
    "--timeout", default=10, help="Timeout in seconds for health check (default: 10)"
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Show verbose output including raw responses"
)
def health(node, timeout, verbose):
    """Check the health status of Calimero nodes."""
    manager = DockerManager()

    if node:
        # Check specific node
        check_node_running(node, manager)
        admin_url = get_node_rpc_url(node, manager)
        console.print(f"[blue]Checking health of node {node} via {admin_url}[/blue]")

        result = run_async_function(check_node_health, admin_url)

        if result["success"]:
            display_health_results([(node, result)], verbose)
        else:
            console.print(f"\n[red]✗ Failed to check health of {node}[/red]")
            console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
            sys.exit(1)
    else:
        # Check all running nodes
        running_nodes = manager.get_running_nodes()

        if not running_nodes:
            console.print("[yellow]No running Calimero nodes found.[/yellow]")
            return

        console.print(
            f"[blue]Checking health of {len(running_nodes)} running node(s)...[/blue]"
        )

        health_results = []
        for node_name in running_nodes:
            try:
                admin_url = get_node_rpc_url(node_name, manager)
                result = run_async_function(check_node_health, admin_url)
                health_results.append((node_name, result))
            except Exception as e:
                health_results.append((node_name, {"success": False, "error": str(e)}))

        display_health_results(health_results, verbose)


def display_health_results(health_results, verbose):
    """Display health check results in a table."""
    table = Table(title="Calimero Node Health Status", box=box.ROUNDED)
    table.add_column("Node", style="cyan")
    table.add_column("Health", style="green")
    table.add_column("Authenticated", style="yellow")
    table.add_column("Peers", style="blue")
    table.add_column("Status", style="white")

    for node_name, result in health_results:
        if result["success"]:
            health_status = extract_health_status(result["health"])
            auth_status = extract_auth_status(result["authenticated"])
            peers_count = extract_peers_count(result["peers"])
            status = "Healthy"

            # Color coding for health status
            if health_status == "healthy":
                health_style = "[green]Healthy[/green]"
            elif health_status == "unhealthy":
                health_style = "[red]Unhealthy[/red]"
                status = "Unhealthy"
            else:
                health_style = f"[yellow]{health_status}[/yellow]"

            # Color coding for authentication
            if auth_status is True:
                auth_style = "[green]Yes[/green]"
            elif auth_status is False:
                auth_style = "[red]No[/red]"
            else:
                auth_style = f"[yellow]{auth_status}[/yellow]"

            table.add_row(node_name, health_style, auth_style, str(peers_count), status)
        else:
            table.add_row(
                node_name,
                "[red]Error[/red]",
                "[red]Error[/red]",
                "[red]Error[/red]",
                "[red]Failed[/red]",
            )

    console.print(table)

    if verbose:
        console.print("\n[bold]Detailed Results:[/bold]")
        for node_name, result in health_results:
            console.print(f"\n[cyan]{node_name}:[/cyan]")
            console.print(f"  {result}")
