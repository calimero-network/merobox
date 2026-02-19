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
import time
from pathlib import Path
from typing import Optional

import click
import docker
from rich import box
from rich.table import Table

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.constants import DEFAULT_DATA_DIR_PREFIX, NUKE_STOP_TIMEOUT
from merobox.commands.manager import DockerManager
from merobox.commands.utils import console, format_file_size

PID_DIR = Path(DEFAULT_DATA_DIR_PREFIX) / ".pids"
EMPTY_DATA_DIR_MIN_AGE_SECONDS = 5


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


class NodeDetectionError(Exception):
    """Raised when node detection fails and stale cleanup should not proceed."""

    pass


class DetectionResult:
    """Result of running node detection with status information."""

    def __init__(
        self,
        nodes: set,
        docker_failed: bool = False,
        binary_failed: bool = False,
        warnings: list = None,
    ):
        self.nodes = nodes
        self.docker_failed = docker_failed
        self.binary_failed = binary_failed
        self.warnings = warnings or []

    @property
    def partial_failure(self) -> bool:
        """True if one detection method failed but not both."""
        return (self.docker_failed or self.binary_failed) and not (
            self.docker_failed and self.binary_failed
        )

    @property
    def complete_failure(self) -> bool:
        """True if both detection methods failed."""
        return self.docker_failed and self.binary_failed


def get_running_node_names(fail_safe: bool = True, silent: bool = False) -> set:
    """
    Get names of all currently running nodes (Docker containers and binary processes).

    Args:
        fail_safe: If True, raise NodeDetectionError when both detection methods fail
                   to prevent accidental deletion of data from running nodes.
        silent: If True, suppress warning output to console.

    Returns:
        Set of node names that are currently running.

    Raises:
        NodeDetectionError: If fail_safe=True and both detection mechanisms fail.
    """
    result = _detect_running_nodes()

    # Output warnings if not silent
    if not silent:
        for warning in result.warnings:
            console.print(f"[yellow]⚠️  Warning: {warning}[/yellow]")

        # Warn about partial failure (one method worked, one failed)
        if result.partial_failure:
            if result.docker_failed:
                console.print(
                    "[yellow]⚠️  Docker detection failed - Docker containers may not be detected[/yellow]"
                )
            if result.binary_failed:
                console.print(
                    "[yellow]⚠️  Binary detection failed - binary processes may not be detected[/yellow]"
                )

    # If both detection mechanisms failed and fail_safe is enabled, raise an error
    if fail_safe and result.complete_failure:
        raise NodeDetectionError(
            "Both Docker and binary process detection failed. "
            "Cannot safely determine running nodes. "
            "Use --force to override this safety check."
        )

    return result.nodes


def _detect_running_nodes(
    manager: Optional[DockerManager] = None,
) -> DetectionResult:
    """
    Internal function to detect running nodes without side effects.

    Args:
        manager: Optional DockerManager instance to reuse an existing connection.
                 If None, a new connection will be created.

    Returns:
        DetectionResult containing nodes found and any warnings/errors.

    Note:
        If the PID directory (./data/.pids) does not exist, binary detection is
        skipped and treated as "no binary nodes running" rather than a detection
        failure. This is intentional: if no PID directory exists, there are no
        binary processes to detect.
    """
    running_nodes = set()
    docker_check_failed = False
    binary_check_failed = False
    warnings = []

    # Check Docker containers
    docker_manager = manager
    created_local_manager = False
    try:
        if docker_manager is None:
            docker_manager = DockerManager(enable_signal_handlers=False)
            created_local_manager = True
        containers = docker_manager.client.containers.list(
            filters={"label": "calimero.node=true", "status": "running"}
        )
        for container in containers:
            running_nodes.add(container.name)
    except SystemExit as e:
        warnings.append(f"Docker check failed: Docker manager exited ({e})")
        docker_check_failed = True
    except docker.errors.DockerException as e:
        warnings.append(f"Docker check failed: {e}")
        docker_check_failed = True
    except Exception as e:
        warnings.append(f"Docker check failed unexpectedly: {e}")
        docker_check_failed = True
    finally:
        if (
            created_local_manager
            and docker_manager
            and hasattr(docker_manager, "client")
        ):
            try:
                docker_manager.client.close()
            except Exception:
                pass

    # Check binary processes via PID files
    # Note: If PID directory doesn't exist, we treat it as "no binary nodes"
    # rather than a detection failure, since there's nothing to detect.
    pid_dir = PID_DIR
    if pid_dir.exists():
        try:
            binary_manager = BinaryManager(
                require_binary=False, enable_signal_handlers=False
            )
            for pid_file in pid_dir.glob("*.pid"):
                node_name = pid_file.stem
                try:
                    if binary_manager.is_node_running(node_name):
                        running_nodes.add(node_name)
                except Exception as e:
                    warnings.append(f"Failed to check binary process {node_name}: {e}")
        except Exception as e:
            warnings.append(f"Binary process check failed: {e}")
            binary_check_failed = True

    return DetectionResult(
        nodes=running_nodes,
        docker_failed=docker_check_failed,
        binary_failed=binary_check_failed,
        warnings=warnings,
    )


def _is_valid_calimero_data_dir(data_dir: str) -> bool:
    """
    Validate that a directory appears to be legitimate Calimero node data.

    Checks for expected subdirectories or marker files to reduce risk of
    deleting unrelated data that happens to match the name prefix.

    Args:
        data_dir: Path to the data directory to validate

    Returns:
        True if the directory appears to contain Calimero data.
    """
    dir_path = Path(data_dir)
    if not dir_path.is_dir() or dir_path.is_symlink():
        return False

    # Check for expected Calimero node subdirectories or files
    # A valid Calimero data directory typically contains a subdirectory
    # named after the node, or specific files like config.toml
    node_name = dir_path.name
    expected_subdir = dir_path / node_name

    # Accept if we find the expected node subdirectory structure
    if expected_subdir.is_dir():
        return True

    # Also accept directories with logs subdirectory (created by binary manager)
    logs_dir = dir_path / "logs"
    if logs_dir.is_dir():
        return True

    # Accept old empty directories, but skip very recent ones to reduce
    # deletion risk during node startup races.
    if not any(dir_path.iterdir()):
        dir_age_seconds = time.time() - dir_path.stat().st_mtime
        return dir_age_seconds >= EMPTY_DATA_DIR_MIN_AGE_SECONDS

    return False


def _filter_still_stale_dirs(
    data_dirs: list, fail_safe: bool = True, silent: bool = False
) -> list:
    """Exclude directories whose nodes became active since stale preview."""
    if not data_dirs:
        return []

    running_nodes = get_running_node_names(fail_safe=fail_safe, silent=True)
    stale_dirs = []
    resumed_dirs = []

    for data_dir in data_dirs:
        node_name = os.path.basename(data_dir)
        if node_name in running_nodes:
            resumed_dirs.append(data_dir)
        else:
            stale_dirs.append(data_dir)

    if resumed_dirs and not silent:
        console.print(
            f"[yellow]⚠️  Skipping {len(resumed_dirs)} directory(ies) that became active since preview[/yellow]"
        )

    return stale_dirs


def find_stale_data_dirs(
    prefix: str = None, fail_safe: bool = True, silent: bool = False
) -> list:
    """
    Find stale/orphan data directories that don't have a running node.

    These are directories that remain from crashed runs or nodes that were
    stopped without proper cleanup.

    Note: There is a potential TOCTOU (time-of-check-time-of-use) race condition
    where a node could start between when we check for running nodes and when
    we delete directories. For safety, use --dry-run first to preview what
    would be deleted.

    Args:
        prefix: Optional prefix to filter data directories
        fail_safe: If True, raise NodeDetectionError when detection fails
        silent: If True, suppress warning output

    Returns:
        List of paths to stale data directories.

    Raises:
        NodeDetectionError: If fail_safe=True and node detection fails.
    """
    all_data_dirs = find_calimero_data_dirs(prefix)
    running_nodes = get_running_node_names(fail_safe=fail_safe, silent=silent)

    stale_dirs = []
    for data_dir in all_data_dirs:
        node_name = os.path.basename(data_dir)
        if node_name not in running_nodes:
            # Validate the directory contains expected Calimero data
            if _is_valid_calimero_data_dir(data_dir):
                stale_dirs.append(data_dir)
            elif not silent:
                console.print(
                    f"[yellow]⚠️  Skipping {data_dir}: does not appear to be valid Calimero data[/yellow]"
                )

    return stale_dirs


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


def _stop_running_services(
    manager: Optional[DockerManager], data_dirs: list, silent: bool = False
) -> None:
    """
    Stop running binary processes and Docker containers before deletion.

    Args:
        manager: DockerManager instance (optional, for Docker operations)
        data_dirs: List of data directories whose nodes should be stopped
        silent: If True, suppress output
    """
    # Stop running binary processes first (don't require binary for cleanup)
    binary_manager = BinaryManager(require_binary=False, enable_signal_handlers=False)
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
                    container.stop(timeout=NUKE_STOP_TIMEOUT)
                    docker_nodes_stopped += 1
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError as e:
                if not silent:
                    console.print(
                        f"[yellow]⚠️  Warning: Could not stop Docker container {node_name}: {e}[/yellow]"
                    )

        if docker_nodes_stopped > 0 and not silent:
            console.print(
                f"[yellow]Stopped {docker_nodes_stopped} Docker container(s)[/yellow]"
            )


def _cleanup_auth_services(
    manager: Optional[DockerManager], silent: bool = False
) -> None:
    """
    Stop and remove auth service stack (Docker only).

    Args:
        manager: DockerManager instance
        silent: If True, suppress output
    """
    if not manager or not hasattr(manager, "client"):
        return

    try:
        auth_container = manager.client.containers.get("auth")
        if not silent:
            console.print("[yellow]Stopping auth service...[/yellow]")
        auth_container.stop(timeout=NUKE_STOP_TIMEOUT)
        auth_container.remove()
        if not silent:
            console.print("[green]✓ Auth service stopped and removed[/green]")
    except docker.errors.NotFound:
        pass
    except docker.errors.APIError as e:
        if not silent:
            console.print(
                f"[yellow]⚠️  Warning: Could not remove auth service: {e}[/yellow]"
            )

    try:
        proxy_container = manager.client.containers.get("proxy")
        if not silent:
            console.print("[yellow]Stopping Traefik proxy...[/yellow]")
        proxy_container.stop(timeout=NUKE_STOP_TIMEOUT)
        proxy_container.remove()
        if not silent:
            console.print("[green]✓ Traefik proxy stopped and removed[/green]")
    except docker.errors.NotFound:
        pass
    except docker.errors.APIError as e:
        if not silent:
            console.print(
                f"[yellow]⚠️  Warning: Could not remove Traefik proxy: {e}[/yellow]"
            )

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
    except docker.errors.APIError as e:
        if not silent:
            console.print(
                f"[yellow]⚠️  Warning: Could not remove auth data volume: {e}[/yellow]"
            )


def execute_nuke(
    manager: Optional[DockerManager] = None,
    prefix: Optional[str] = None,
    verbose: bool = False,
    silent: bool = False,
    stale_only: bool = False,
    force: bool = False,
    precomputed_data_dirs: Optional[list] = None,
) -> bool:
    """
    Execute the nuke operation programmatically (for use in workflows).

    Args:
        manager: DockerManager or BinaryManager instance (optional)
        prefix: Optional prefix to filter which nodes to nuke
        verbose: Enable verbose output
        silent: Suppress most output (for workflow automation)
        stale_only: If True, only clean up stale/orphan directories (not running nodes)
        force: If True, skip fail-safe checks for node detection
        precomputed_data_dirs: Optional precomputed list of directories to delete.
            Used by CLI flow to avoid re-running stale directory discovery between
            preview and deletion confirmation.

    Returns:
        bool: True if nuke succeeded, False otherwise
    """
    try:
        if precomputed_data_dirs is not None:
            data_dirs = list(precomputed_data_dirs)
        elif stale_only:
            try:
                data_dirs = find_stale_data_dirs(
                    prefix, fail_safe=not force, silent=silent
                )
            except NodeDetectionError as e:
                if not silent:
                    console.print(f"[red]❌ {e}[/red]")
                return False
            if not data_dirs:
                if not silent:
                    console.print("[yellow]No stale data directories found.[/yellow]")
                return True
            if not silent:
                console.print(
                    f"[red]Found {len(data_dirs)} stale data directory(ies)[/red]"
                )
        else:
            data_dirs = find_calimero_data_dirs(prefix)

        if stale_only and data_dirs:
            # Re-check running nodes right before deletion to avoid deleting a
            # directory for a node that started after stale preview.
            try:
                data_dirs = _filter_still_stale_dirs(
                    data_dirs, fail_safe=not force, silent=silent
                )
            except NodeDetectionError as e:
                if not silent:
                    console.print(f"[red]❌ {e}[/red]")
                return False

            if not data_dirs:
                if not silent:
                    console.print(
                        "[yellow]No stale data directories found at deletion time.[/yellow]"
                    )
                return True

        if not data_dirs:
            if not silent:
                console.print(
                    "[yellow]No Calimero node data directories found.[/yellow]"
                )
            return True

        if not silent and not stale_only:
            console.print(
                f"[red]Found {len(data_dirs)} Calimero node data directory(ies)[/red]"
            )

        # For stale_only mode, skip stopping processes (directories are already orphaned)
        if not stale_only:
            _stop_running_services(manager, data_dirs, silent)
            _cleanup_auth_services(manager, silent)

        # Delete data directories
        dir_type = "stale" if stale_only else "data"
        if not silent:
            console.print(
                f"\n[red]Deleting {len(data_dirs)} {dir_type} directory(ies)...[/red]"
            )

        results = nuke_all_data_dirs(data_dirs, dry_run=False)

        deleted_count = sum(1 for r in results if r["status"] == "deleted")
        total_deleted_size = sum(
            r["size_bytes"] for r in results if r["status"] == "deleted"
        )

        if not silent:
            if deleted_count > 0:
                console.print(
                    f"[green]✓ Successfully deleted {deleted_count} {dir_type} directory(ies)[/green]"
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
@click.option(
    "--stale",
    is_flag=True,
    help="Only clean up stale/orphan directories not matching any running node",
)
def nuke(dry_run, force, verbose, prefix, stale):
    """Delete all Calimero node data folders for complete reset."""

    if stale:
        try:
            # Use fail_safe=True unless --force is specified
            data_dirs = find_stale_data_dirs(prefix, fail_safe=not force, silent=False)
        except NodeDetectionError as e:
            console.print(f"[red]❌ {e}[/red]")
            return
        if not data_dirs:
            console.print("[yellow]No stale data directories found.[/yellow]")
            return
        console.print(f"[red]Found {len(data_dirs)} stale data directory(ies):[/red]")
    else:
        data_dirs = find_calimero_data_dirs(prefix)
        if not data_dirs:
            console.print("[yellow]No Calimero node data directories found.[/yellow]")
            return
        console.print(
            f"[red]Found {len(data_dirs)} Calimero node data directory(ies):[/red]"
        )

    table_title = (
        "Stale Data Directories" if stale else "Calimero Node Data Directories"
    )
    table = Table(title=table_title, box=box.ROUNDED)
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

    # Calculate auth volume size if it exists using Docker (only for full nuke)
    manager = None
    auth_volume_size = 0
    if not stale:
        try:
            manager = DockerManager(enable_signal_handlers=False)
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

        # Check what auth services would be cleaned up (only for full nuke, not stale)
        if not stale:
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
        if stale:
            console.print(
                "\n[red]⚠️  WARNING: This will permanently delete stale data directories![/red]"
            )
        else:
            console.print(
                "\n[red]⚠️  WARNING: This will permanently delete ALL Calimero node data![/red]"
            )
        console.print("[red]This action cannot be undone.[/red]")

        confirm = input("\nType 'YES' to confirm deletion: ")
        if confirm != "YES":
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

    # Use the execute_nuke function
    if execute_nuke(
        manager,
        prefix=prefix,
        verbose=verbose,
        silent=False,
        stale_only=stale,
        force=force,
        precomputed_data_dirs=data_dirs,
    ):
        if stale:
            console.print("\n[green]Stale directories cleaned up successfully.[/green]")
        else:
            console.print("\n[blue]To start fresh, run:[/blue]")
            console.print("[blue]  merobox run[/blue]")
    else:
        console.print("\n[red]❌ Nuke operation failed[/red]")


if __name__ == "__main__":
    nuke()  # pylint: disable=no-value-for-parameter
