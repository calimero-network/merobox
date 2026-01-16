"""
Install command - Install applications on Calimero nodes using admin API.
"""

import os
import shutil
import sys
from typing import Optional
from urllib.parse import urlparse

import click
import docker

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import (
    CONTAINER_DATA_DIR_PATTERNS,
    DEFAULT_METADATA,
)
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.utils import (
    check_node_running,
    console,
    get_node_rpc_url,
)


def _prepare_container_path(
    node_name: str, source_path: str, manager: DockerManager
) -> Optional[str]:
    """Prepare container path for dev installation by copying file to container data directory."""
    container_data_dir: Optional[str] = None

    # Get data directory from Docker container's volume mounts
    if hasattr(manager, "client"):
        try:
            container = manager.client.containers.get(node_name)
            mounts = container.attrs.get("Mounts", [])
            for mount in mounts:
                if mount.get("Destination") == "/app/data":
                    mount_source = mount.get("Source")
                    if mount_source and os.path.exists(mount_source):
                        container_data_dir = mount_source
                        break
        except (
            docker.errors.NotFound,
            docker.errors.APIError,
            AttributeError,
            KeyError,
        ):
            pass

    # Fallback to pattern matching if not found from container or path doesn't exist
    if not container_data_dir or not os.path.exists(container_data_dir):
        for pattern in CONTAINER_DATA_DIR_PATTERNS:
            if "{prefix}-{node_num}-{chain_id}" in pattern:
                parts = node_name.split("-")
                if len(parts) >= 3:
                    candidate = pattern.format(
                        prefix=parts[0], node_num=parts[1], chain_id=parts[2]
                    )
                else:
                    candidate = None
            elif "{node_name}" in pattern:
                candidate = pattern.format(node_name=node_name)
            else:
                candidate = None

            if candidate and os.path.exists(candidate):
                container_data_dir = candidate
                break

    if not container_data_dir or not os.path.exists(container_data_dir):
        console.print(f"[red]Container data directory not found for {node_name}[/red]")
        return None

    try:
        abs_container_data_dir = os.path.abspath(container_data_dir)
        abs_source_path = os.path.abspath(source_path)
        # Check if source is already in container data directory
        if (
            os.path.commonpath([abs_source_path, abs_container_data_dir])
            == abs_container_data_dir
        ):
            # Preserve subdirectory structure relative to container data directory
            # Use forward slashes for Linux container paths (Windows returns backslashes)
            relative_path = os.path.relpath(abs_source_path, abs_container_data_dir)
            return f"/app/data/{relative_path.replace(os.sep, '/')}"
    except ValueError:
        pass

    # Copy file to container data directory
    filename = os.path.basename(source_path)
    try:
        os.makedirs(container_data_dir, exist_ok=True)
        container_file_path = os.path.join(container_data_dir, filename)
        shutil.copy2(source_path, container_file_path)
        return f"/app/data/{filename}"
    except (OSError, shutil.Error) as error:
        console.print(
            f"[red]Failed to copy file to container data directory: {error}[/red]"
        )
        return None


def validate_installation_source(
    url: str = None, path: str = None, is_dev: bool = False
) -> tuple[bool, str]:
    """Validate that either URL or path is provided based on installation type."""
    if is_dev:
        if not path:
            return False, "Development installation requires --path parameter"
        if not os.path.exists(path):
            return False, f"File not found: {path}"
        if not os.path.isfile(path):
            return False, f"Path is not a file: {path}"
        return True, ""
    else:
        if not url:
            return False, "Remote installation requires --url parameter"
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False, f"Invalid URL format: {url}"
            return True, ""
        except (ValueError, AttributeError):
            return False, f"Invalid URL: {url}"


@click.command()
@click.option(
    "--node", "-n", required=True, help="Node name to install the application on"
)
@click.option("--url", help="URL to install the application from")
@click.option("--path", help="Local path for dev installation")
@click.option(
    "--dev", is_flag=True, help="Install as development application from local path"
)
@click.option("--metadata", help="Application metadata (optional)")
@click.option(
    "--timeout", default=30, help="Timeout in seconds for installation (default: 30)"
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def install(node, url, path, dev, metadata, timeout, verbose):
    """Install applications on Calimero nodes."""
    manager = DockerManager()

    # Check if node is running
    check_node_running(node, manager)

    # Validate installation source
    is_valid, error_msg = validate_installation_source(url, path, dev)
    if not is_valid:
        console.print(f"[red]✗ {error_msg}[/red]")
        sys.exit(1)

    # Parse metadata if provided
    metadata_bytes = DEFAULT_METADATA
    if metadata:
        try:
            metadata_bytes = metadata.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as e:
            console.print(f"[red]✗ Failed to encode metadata: {str(e)}[/red]")
            sys.exit(1)

    # Get admin API URL
    rpc_url = get_node_rpc_url(node, manager)

    # Execute installation using calimero-client-py
    try:
        client = get_client_for_rpc_url(rpc_url)

        if dev and path:
            application_path = os.path.abspath(os.path.expanduser(path))
            if not os.path.isfile(application_path):
                console.print(
                    f"[red]✗ Application path not found or not a file: {application_path}[/red]"
                )
                sys.exit(1)

            container_path = _prepare_container_path(node, application_path, manager)
            if not container_path:
                console.print(
                    "[red]✗ Unable to prepare application file inside container data directory[/red]"
                )
                sys.exit(1)

            api_result = client.install_dev_application(
                path=container_path, metadata=metadata_bytes
            )
        else:
            api_result = client.install_application(url=url, metadata=metadata_bytes)

        result = ok(api_result)
    except Exception as e:
        result = fail("install_application failed", error=e)

    if result["success"]:
        console.print("\n[green]✓ Application installed successfully![/green]")

        if verbose:
            console.print("\n[bold]Installation response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to install application[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        if verbose and "exception" in result:
            exc = result["exception"]
            console.print(f"[red]Exception Type: {exc.get('type', 'Unknown')}[/red]")
            console.print(
                f"[red]Exception Message: {exc.get('message', 'No message')}[/red]"
            )
            if "traceback" in exc:
                console.print("\n[bold]Traceback:[/bold]")
                console.print(f"[red]{exc['traceback']}[/red]")

        sys.exit(1)
