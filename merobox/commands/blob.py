"""
Blob command - Manage Calimero blob storage.
"""

import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.manager import DockerManager
from merobox.commands.utils import console, get_node_rpc_url


@click.group()
def blob():
    """Manage Calimero blob storage."""


def _get_manager_and_rpc_url(node: str, no_docker: bool = False):
    """Get the appropriate manager and RPC URL for the node."""
    if no_docker:
        manager = BinaryManager()
    else:
        manager = DockerManager()

    rpc_url = get_node_rpc_url(node, manager)
    return manager, rpc_url


def _get_client_for_node(node: str, no_docker: bool = False):
    """Get client for node using appropriate manager."""
    _, rpc_url = _get_manager_and_rpc_url(node, no_docker)
    client = get_client_for_rpc_url(rpc_url)
    return client, rpc_url


@blob.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option(
    "--file", "-f", required=True, type=click.File("rb"), help="File to upload"
)
@click.option("--context-id", "-c", help="Optional context ID to associate with blob")
@click.option("--no-docker", is_flag=True, help="Use binary mode instead of Docker")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def upload(node, file, context_id, no_docker, verbose):
    """Upload a file to blob storage."""
    mode = "binary mode" if no_docker else "Docker mode"
    console.print(f"[blue]Uploading file to blob storage on {node} ({mode})[/blue]")

    try:
        # Read file data
        file_data = file.read()
        file_size = len(file_data)
        console.print(
            f"[cyan]File size: {file_size} bytes ({file_size / 1024:.2f} KB)[/cyan]"
        )

        if context_id:
            console.print(f"[cyan]Context ID: {context_id}[/cyan]")

        # Get client using appropriate manager
        client, _ = _get_client_for_node(node, no_docker)

        # Upload blob
        result = client.upload_blob(file_data, context_id)

        # Extract blob info
        if isinstance(result, dict) and "payload" in result:
            blob_info = result["payload"]["data"]
        elif isinstance(result, dict) and "data" in result:
            blob_info = result["data"]
        else:
            blob_info = result

        blob_id = blob_info.get("blob_id")
        blob_size = blob_info.get("size")

        console.print("\n[green]✓ Blob uploaded successfully![/green]")

        # Create table
        table = Table(title="Blob Upload Result", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Blob ID", str(blob_id))
        table.add_row("Size", f"{blob_size} bytes ({blob_size / 1024:.2f} KB)")
        if context_id:
            table.add_row("Context ID", context_id)
        table.add_row("Node", node)
        table.add_row("Mode", mode)

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    except Exception as e:
        console.print(f"\n[red]✗ Failed to upload blob: {str(e)}[/red]")
        sys.exit(1)


@blob.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--blob-id", "-b", required=True, help="Blob ID to download")
@click.option(
    "--output", "-o", required=True, type=click.File("wb"), help="Output file"
)
@click.option("--context-id", "-c", help="Optional context ID for network discovery")
@click.option("--no-docker", is_flag=True, help="Use binary mode instead of Docker")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def download(node, blob_id, output, context_id, no_docker, verbose):
    """Download a blob from storage."""
    mode = "binary mode" if no_docker else "Docker mode"
    console.print(f"[blue]Downloading blob {blob_id} from {node} ({mode})[/blue]")

    if context_id:
        console.print(f"[cyan]Context ID: {context_id}[/cyan]")

    try:
        # Get client using appropriate manager
        client, _ = _get_client_for_node(node, no_docker)

        # Download blob
        blob_data = client.download_blob(blob_id, context_id)

        # Write to file
        output.write(blob_data)
        output_size = len(blob_data)

        console.print("\n[green]✓ Blob downloaded successfully![/green]")
        console.print(
            f"[green]Downloaded {output_size} bytes ({output_size / 1024:.2f} KB)[/green]"
        )
        console.print(f"[green]Saved to: {output.name}[/green]")

        if verbose:
            console.print("\n[bold]Download details:[/bold]")
            console.print(f"Blob ID: {blob_id}")
            console.print(f"Node: {node}")
            console.print(f"Mode: {mode}")
            if context_id:
                console.print(f"Context ID: {context_id}")

    except Exception as e:
        console.print(f"\n[red]✗ Failed to download blob: {str(e)}[/red]")
        sys.exit(1)


@blob.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--context-id", "-c", help="Optional context ID to filter blobs")
@click.option("--no-docker", is_flag=True, help="Use binary mode instead of Docker")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_blobs(node, context_id, no_docker, verbose):
    """List all blobs in storage."""
    mode = "binary mode" if no_docker else "Docker mode"
    console.print(f"[blue]Listing blobs on {node} ({mode})[/blue]")

    if context_id:
        console.print(f"[cyan]Context ID: {context_id}[/cyan]")

    try:
        # Get client using appropriate manager
        client, _ = _get_client_for_node(node, no_docker)

        # List blobs
        result = client.list_blobs()

        # Extract blobs list
        if isinstance(result, dict) and "payload" in result:
            blobs_data = result["payload"]["data"]
        elif isinstance(result, dict) and "data" in result:
            blobs_data = result["data"]
        else:
            blobs_data = result

        blobs = blobs_data.get("blobs", []) if isinstance(blobs_data, dict) else []

        console.print(f"\n[green]✓ Found {len(blobs)} blobs[/green]")

        if blobs:
            # Create table
            table = Table(title=f"Blobs on {node} ({mode})", box=box.ROUNDED)
            table.add_column("Blob ID", style="cyan")
            table.add_column("Size", style="yellow")
            table.add_column("Created", style="green")

            for blob_item in blobs:
                blob_id = blob_item.get("blob_id", "Unknown")
                size = blob_item.get("size", 0)
                created = blob_item.get("created_at", "Unknown")

                table.add_row(
                    blob_id, f"{size} bytes ({size / 1024:.2f} KB)", str(created)
                )

            console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    except Exception as e:
        console.print(f"\n[red]✗ Failed to list blobs: {str(e)}[/red]")
        sys.exit(1)


@blob.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--blob-id", "-b", required=True, help="Blob ID to get info for")
@click.option("--no-docker", is_flag=True, help="Use binary mode instead of Docker")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def info(node, blob_id, no_docker, verbose):
    """Get blob metadata."""
    mode = "binary mode" if no_docker else "Docker mode"
    console.print(f"[blue]Getting info for blob {blob_id} on {node} ({mode})[/blue]")

    try:
        # Get client using appropriate manager
        client, _ = _get_client_for_node(node, no_docker)

        # Get blob info
        result = client.get_blob_info(blob_id)

        # Extract blob info
        if isinstance(result, dict) and "payload" in result:
            blob_info = result["payload"]["data"]
        elif isinstance(result, dict) and "data" in result:
            blob_info = result["data"]
        else:
            blob_info = result

        console.print("\n[green]✓ Blob info retrieved successfully![/green]")

        # Create table
        table = Table(title="Blob Information", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        for key, value in blob_info.items():
            table.add_row(str(key), str(value))

        table.add_row("Node", node)
        table.add_row("Mode", mode)

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    except Exception as e:
        console.print(f"\n[red]✗ Failed to get blob info: {str(e)}[/red]")
        sys.exit(1)


@blob.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--blob-id", "-b", required=True, help="Blob ID to delete")
@click.option("--no-docker", is_flag=True, help="Use binary mode instead of Docker")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def delete(node, blob_id, no_docker, verbose, yes):
    """Delete a blob from storage."""
    mode = "binary mode" if no_docker else "Docker mode"

    if not yes:
        confirm = click.confirm(
            f"Are you sure you want to delete blob {blob_id} from {node} ({mode})?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Deletion cancelled[/yellow]")
            return

    console.print(f"[blue]Deleting blob {blob_id} from {node} ({mode})[/blue]")

    try:
        # Get client using appropriate manager
        client, _ = _get_client_for_node(node, no_docker)

        # Delete blob
        result = client.delete_blob(blob_id)

        # Check deletion result
        if isinstance(result, dict):
            deleted = result.get("deleted", False)
            if deleted:
                console.print("\n[green]✓ Blob deleted successfully![/green]")
            else:
                console.print("\n[yellow]⚠ Blob deletion status unclear[/yellow]")
        else:
            console.print("\n[green]✓ Blob deletion completed[/green]")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    except Exception as e:
        console.print(f"\n[red]✗ Failed to delete blob: {str(e)}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    blob()
