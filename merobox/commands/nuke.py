"""
Nuke command - Delete all Calimero node data folders for complete reset.

This module provides both CLI and programmatic interfaces for complete data cleanup:

1. CLI Command (`merobox nuke`):
   - Interactive deletion with confirmation prompt
   - Supports dry-run, force, verbose, and prefix filtering
   - Shows detailed statistics about what will be deleted

2. Programmatic Interface (`execute_nuke()`):
   - Used by workflow executor for `nuke_on_start` and `nuke_on_end`
   - Silent mode for automation
   - Prefix-based filtering for workflow isolation

Workflow Integration:
   - `nuke_on_start: true` - Clean slate before workflow execution
   - `nuke_on_end: true` - Complete cleanup after workflow completion

What Gets Nuked:
   - Node data directories (matching prefix pattern)
   - Running Docker containers (nodes, auth, proxy)
   - Docker volumes (auth_data)

See NUKE_DOCUMENTATION.md for comprehensive usage guide.
"""

import os
import shutil
from pathlib import Path

import click
import docker
from rich import box
from rich.table import Table

from merobox.commands.manager import DockerManager
from merobox.commands.utils import console, format_file_size


def find_calimero_data_dirs(prefix: str = None) -> list:
    """
    Find all Calimero node data directories.

    Args:
        prefix: Optional prefix to filter data directories (e.g., "prop-test-")
                If None, finds all directories starting with "calimero-node-" or any known prefix
    """
    data_dirs = []
    data_path = Path("data")

    if not data_path.exists():
        return data_dirs

    for item in data_path.iterdir():
        if item.is_dir():
            if prefix:
                # Filter by specific prefix
                if item.name.startswith(prefix):
                    data_dirs.append(str(item))
            else:
                # Default: find all Calimero-related directories
                if (
                    item.name.startswith("calimero-node-")
                    or item.name.startswith("prop-")
                    or item.name.startswith("proposal-")
                ):
                    data_dirs.append(str(item))

    return data_dirs


def nuke_all_data_dirs(data_dirs: list, dry_run: bool = False) -> dict:
    """Delete all Calimero node data directories."""
    results = []

    for data_dir in data_dirs:
        try:
            if dry_run:
                if os.path.exists(data_dir):
                    dir_size = sum(
                        f.stat().st_size
                        for f in Path(data_dir).rglob("*")
                        if f.is_file()
                    )
                    results.append(
                        {
                            "path": data_dir,
                            "status": "would_delete",
                            "size_bytes": dir_size,
                        }
                    )
                else:
                    results.append(
                        {"path": data_dir, "status": "not_found", "size_bytes": 0}
                    )
            else:
                if os.path.exists(data_dir):
                    dir_size = sum(
                        f.stat().st_size
                        for f in Path(data_dir).rglob("*")
                        if f.is_file()
                    )
                    shutil.rmtree(data_dir)
                    results.append(
                        {"path": data_dir, "status": "deleted", "size_bytes": dir_size}
                    )
                else:
                    results.append(
                        {"path": data_dir, "status": "not_found", "size_bytes": 0}
                    )
        except Exception as e:
            results.append(
                {"path": data_dir, "status": "error", "error": str(e), "size_bytes": 0}
            )

    return results


def execute_nuke(
    manager=None,
    prefix: str = None,
    verbose: bool = False,
    silent: bool = False,
) -> bool:
    """
    Execute the nuke operation programmatically (for use in workflows).

    Args:
        manager: DockerManager or BinaryManager instance (optional)
        prefix: Optional prefix to filter which nodes to nuke
        verbose: Enable verbose output
        silent: Suppress most output (for workflow automation)

    Returns:
        bool: True if nuke succeeded, False otherwise
    """
    try:
        from merobox.commands.binary_manager import BinaryManager

        data_dirs = find_calimero_data_dirs(prefix)

        if not data_dirs:
            if not silent:
                console.print(
                    "[yellow]No Calimero node data directories found.[/yellow]"
                )
            return True

        if not silent:
            console.print(
                f"[red]Found {len(data_dirs)} Calimero node data directory(ies)[/red]"
            )

        # Stop running binary processes first (don't require binary for cleanup)
        binary_manager = BinaryManager(require_binary=False)
        binary_nodes_stopped = 0
        for data_dir in data_dirs:
            node_name = os.path.basename(data_dir)
            if binary_manager.is_node_running(node_name):
                if not silent:
                    console.print(
                        f"[yellow]Stopping binary process {node_name}...[/yellow]"
                    )
                if binary_manager.stop_node(node_name):
                    binary_nodes_stopped += 1

        if binary_nodes_stopped > 0 and not silent:
            console.print(
                f"[yellow]Stopped {binary_nodes_stopped} binary process(es)[/yellow]"
            )

        # Stop running Docker containers (if manager is DockerManager)
        docker_nodes_stopped = 0
        if manager and hasattr(manager, "client"):
            for data_dir in data_dirs:
                node_name = os.path.basename(data_dir)
                try:
                    container = manager.client.containers.get(node_name)
                    if container.status == "running":
                        if not silent:
                            console.print(
                                f"[yellow]Stopping Docker container {node_name}...[/yellow]"
                            )
                        container.stop(timeout=30)
                        docker_nodes_stopped += 1
                except Exception:
                    pass

            if docker_nodes_stopped > 0 and not silent:
                console.print(
                    f"[yellow]Stopped {docker_nodes_stopped} Docker container(s)[/yellow]"
                )

        # Stop and remove auth service stack if it exists (Docker only)
        if manager and hasattr(manager, "client"):
            try:
                auth_container = manager.client.containers.get("auth")
                if not silent:
                    console.print("[yellow]Stopping auth service...[/yellow]")
                auth_container.stop(timeout=30)
                auth_container.remove()
                if not silent:
                    console.print("[green]✓ Auth service stopped and removed[/green]")
            except Exception:
                pass

            try:
                proxy_container = manager.client.containers.get("proxy")
                if not silent:
                    console.print("[yellow]Stopping Traefik proxy...[/yellow]")
                proxy_container.stop(timeout=30)
                proxy_container.remove()
                if not silent:
                    console.print("[green]✓ Traefik proxy stopped and removed[/green]")
            except Exception:
                pass

            # Remove auth data volume if it exists
            try:
                auth_volume = manager.client.volumes.get("calimero_auth_data")
                if not silent:
                    console.print("[yellow]Removing auth data volume...[/yellow]")
                auth_volume.remove()
                if not silent:
                    console.print("[green]✓ Auth data volume removed[/green]")
            except docker.errors.NotFound:
                pass
            except Exception as e:
                if not silent:
                    console.print(
                        f"[yellow]⚠️  Warning: Could not remove auth data volume: {e}[/yellow]"
                    )

        # Delete data directories
        if not silent:
            console.print(
                f"\n[red]Deleting {len(data_dirs)} data directory(ies)...[/red]"
            )

        results = nuke_all_data_dirs(data_dirs, dry_run=False)

        deleted_count = sum(1 for r in results if r["status"] == "deleted")
        total_deleted_size = sum(
            r["size_bytes"] for r in results if r["status"] == "deleted"
        )

        if not silent:
            if deleted_count > 0:
                console.print(
                    f"[green]✓ Successfully deleted {deleted_count} data directory(ies)[/green]"
                )
                console.print(
                    f"[green]Total space freed: {format_file_size(total_deleted_size)}[/green]"
                )
            else:
                console.print("[yellow]No data directories were deleted.[/yellow]")

        if verbose and not silent:
            console.print("\n[bold]Verbose Details:[/bold]")
            for result in results:
                console.print(f"  {result['path']}: {result['status']}")

        return True

    except Exception as e:
        if not silent:
            console.print(f"[red]❌ Nuke operation failed: {str(e)}[/red]")
        return False


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting",
)
@click.option(
    "--force", "-f", is_flag=True, help="Force deletion without confirmation prompt"
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
@click.option(
    "--prefix",
    type=str,
    default=None,
    help="Filter nodes by prefix (e.g., 'calimero-node-' or 'prop-test-')",
)
def nuke(dry_run, force, verbose, prefix):
    """Delete all Calimero node data folders for complete reset."""

    data_dirs = find_calimero_data_dirs(prefix)

    if not data_dirs:
        console.print("[yellow]No Calimero node data directories found.[/yellow]")
        return

    console.print(
        f"[red]Found {len(data_dirs)} Calimero node data directory(ies):[/red]"
    )

    table = Table(title="Calimero Node Data Directories", box=box.ROUNDED)
    table.add_column("Directory", style="cyan")
    table.add_column("Status", style="yellow")

    for data_dir in data_dirs:
        table.add_row(data_dir, "Found")

    console.print(table)

    total_size = 0
    auth_volume_size = 0

    # Calculate node data directories size
    for data_dir in data_dirs:
        if os.path.exists(data_dir):
            dir_size = sum(
                f.stat().st_size for f in Path(data_dir).rglob("*") if f.is_file()
            )
            total_size += dir_size

    # Calculate auth volume size if it exists using Docker
    manager = None
    auth_volume_size = 0
    try:
        manager = DockerManager()
        manager.client.volumes.get("calimero_auth_data")
        # Use Docker to calculate the volume size
        try:
            result = manager.client.containers.run(
                "alpine:latest",
                command="sh -c 'du -sb /data 2>/dev/null | cut -f1 || echo 0'",
                volumes={"calimero_auth_data": {"bind": "/data", "mode": "ro"}},
                remove=True,
                detach=False,
            )
            auth_volume_size = int(result.decode().strip())
            total_size += auth_volume_size
            if auth_volume_size > 0:
                console.print(
                    f"[cyan]Auth volume data size: {format_file_size(auth_volume_size)}[/cyan]"
                )
        except Exception as e:
            console.print(
                f"[yellow]Could not calculate auth volume size: {str(e)}[/yellow]"
            )
    except docker.errors.NotFound:
        pass
    except Exception:
        # Docker not available or other error, proceed without manager
        pass

    total_size_formatted = format_file_size(total_size)
    console.print(f"[red]Total data size: {total_size_formatted}[/red]")

    if dry_run:
        console.print("\n[yellow]DRY RUN MODE - No files will be deleted[/yellow]")

        # Check what auth services would be cleaned up
        auth_cleanup_items = []

        if manager:
            try:
                manager.client.containers.get("auth")
                auth_cleanup_items.append("Auth service container")
            except Exception:
                pass

            try:
                manager.client.containers.get("proxy")
                auth_cleanup_items.append("Traefik proxy container")
            except Exception:
                pass

            try:
                manager.client.volumes.get("calimero_auth_data")
                if auth_volume_size > 0:
                    auth_cleanup_items.append(
                        f"Auth data volume ({format_file_size(auth_volume_size)})"
                    )
                else:
                    auth_cleanup_items.append("Auth data volume")
            except Exception:
                pass

        if auth_cleanup_items:
            console.print(
                f"[yellow]Would also clean up: {', '.join(auth_cleanup_items)}[/yellow]"
            )

        console.print("[yellow]Use --force to actually delete the data[/yellow]")
        return

    if not force:
        console.print(
            "\n[red]⚠️  WARNING: This will permanently delete ALL Calimero node data![/red]"
        )
        console.print("[red]This action cannot be undone.[/red]")

        confirm = input("\nType 'YES' to confirm deletion: ")
        if confirm != "YES":
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

    # Use the new execute_nuke function
    if execute_nuke(manager, prefix=prefix, verbose=verbose, silent=False):
        console.print("\n[blue]To start fresh, run:[/blue]")
        console.print("[blue]  merobox run[/blue]")
    else:
        console.print("\n[red]❌ Nuke operation failed[/red]")


if __name__ == "__main__":
    nuke()  # pylint: disable=no-value-for-parameter
