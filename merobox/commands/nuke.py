"""
Nuke command - Delete all Calimero node data folders for complete reset.
"""

import click
import os
import sys
import time
import shutil
from typing import Dict, Any, Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
)
from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import format_file_size, console


def find_calimero_data_dirs() -> list:
    """Find all Calimero node data directories."""
    data_dirs = []
    data_path = Path("data")

    if not data_path.exists():
        return data_dirs

    for item in data_path.iterdir():
        if item.is_dir() and item.name.startswith("calimero-node-"):
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
def nuke(dry_run, force, verbose):
    """Delete all Calimero node data folders for complete reset."""

    data_dirs = find_calimero_data_dirs()

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
    for data_dir in data_dirs:
        if os.path.exists(data_dir):
            dir_size = sum(
                f.stat().st_size for f in Path(data_dir).rglob("*") if f.is_file()
            )
            total_size += dir_size

    total_size_formatted = format_file_size(total_size)
    console.print(f"[red]Total data size: {total_size_formatted}[/red]")

    if dry_run:
        console.print("\n[yellow]DRY RUN MODE - No files will be deleted[/yellow]")
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

    manager = CalimeroManager()
    running_nodes = []

    try:
        for data_dir in data_dirs:
            node_name = os.path.basename(data_dir)
            try:
                container = manager.client.containers.get(node_name)
                if container.status == "running":
                    running_nodes.append(node_name)
                    console.print(f"[yellow]Stopping node {node_name}...[/yellow]")
                    container.stop(timeout=30)
            except Exception:
                pass
    except Exception as e:
        console.print(f"[yellow]Warning: Could not stop nodes: {e}[/yellow]")

    if running_nodes:
        console.print(f"[yellow]Stopped {len(running_nodes)} running node(s)[/yellow]")

    console.print(f"\n[red]Deleting {len(data_dirs)} data directory(ies)...[/red]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Deleting data directories...", total=len(data_dirs))

        results = nuke_all_data_dirs(data_dirs, dry_run=False)

        progress.update(task, description="Deletion completed")

    console.print(f"\n[bold]Deletion Results:[/bold]")

    results_table = Table(title="Deletion Results", box=box.ROUNDED)
    results_table.add_column("Directory", style="cyan")
    results_table.add_column("Status", style="green")
    results_table.add_column("Size", style="yellow")
    results_table.add_column("Details", style="white")

    deleted_count = 0
    total_deleted_size = 0

    for result in results:
        status_style = "green" if result["status"] == "deleted" else "red"
        status_text = result["status"].upper()

        if result["status"] == "deleted":
            deleted_count += 1
            total_deleted_size += result["size_bytes"]
            details = f"Deleted {format_file_size(result['size_bytes'])}"
        elif result["status"] == "error":
            details = f"Error: {result.get('error', 'Unknown')}"
        else:
            details = "Not found"

        results_table.add_row(
            result["path"],
            f"[{status_style}]{status_text}[/{status_style}]",
            format_file_size(result["size_bytes"]),
            details,
        )

    console.print(results_table)

    if deleted_count > 0:
        console.print(
            f"\n[green]✓ Successfully deleted {deleted_count} data directory(ies)[/green]"
        )
        console.print(
            f"[green]Total space freed: {format_file_size(total_deleted_size)}[/green]"
        )
        console.print(f"\n[blue]To start fresh, run:[/blue]")
        console.print(f"[blue]  python3 merobox_cli.py run[/blue]")
    else:
        console.print(f"\n[yellow]No data directories were deleted.[/yellow]")

    if verbose:
        console.print(f"\n[bold]Verbose Details:[/bold]")
        for result in results:
            console.print(f"  {result['path']}: {result['status']}")


if __name__ == "__main__":
    nuke()
