"""
Install command - Install applications on Calimero nodes using admin API.
"""

import os
import sys
from urllib.parse import urlparse

import click

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import DEFAULT_METADATA
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import (
    check_node_running,
    console,
    get_node_rpc_url,
    run_async_function,
)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def install_application_via_admin_api(
    rpc_url: str,
    url: str = None,
    path: str = None,
    metadata: bytes = None,
    is_dev: bool = False,
    node_name: str = None,
) -> dict:
    """Install an application using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        metadata_bytes = metadata or DEFAULT_METADATA

        normalized_path = os.path.abspath(os.path.expanduser(path)) if path else None

        if is_dev and normalized_path:
            console.print(
                f"[blue]Installing development application from path: {normalized_path}[/blue]"
            )
            result = await client.install_dev_application(
                path=normalized_path, metadata=metadata_bytes
            )
        else:
            result = await client.install_application(url=url, metadata=metadata_bytes)

        return ok(data=result)
    except Exception as e:
        return fail(error=e)


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
        except Exception:
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
    metadata_bytes = b""
    if metadata:
        try:
            metadata_bytes = metadata.encode("utf-8")
        except Exception as e:
            console.print(f"[red]✗ Failed to encode metadata: {str(e)}[/red]")
            sys.exit(1)

    # Get admin API URL
    admin_url = get_node_rpc_url(node, manager)

    if dev:
        console.print(
            f"[blue]Installing development application on node {node} via {admin_url}[/blue]"
        )
    else:
        console.print(
            f"[blue]Installing application from {url} on node {node} via {admin_url}[/blue]"
        )

    # Run installation
    result = run_async_function(
        install_application_via_admin_api,
        admin_url,
        url,
        path,
        metadata_bytes,
        dev,
        node,
    )

    if result["success"]:
        console.print("\n[green]✓ Application installed successfully![/green]")

        if dev and "container_path" in result:
            console.print(f"[blue]Container path: {result['container_path']}[/blue]")

        if verbose:
            console.print("\n[bold]Installation response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to install application[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)
