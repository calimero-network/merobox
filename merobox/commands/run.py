"""
Run command - Start Calimero node(s) in Docker containers.
"""

import sys

import click
from rich.console import Console

from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import validate_port

console = Console()


@click.command()
@click.option("--count", "-c", default=1, help="Number of nodes to run (default: 1)")
@click.option("--base-port", "-p", help="Base P2P port (auto-detect if not specified)")
@click.option(
    "--base-rpc-port", "-r", help="Base RPC port (auto-detect if not specified)"
)
@click.option("--chain-id", default="testnet-1", help="Chain ID (default: testnet-1)")
@click.option(
    "--prefix",
    default="calimero-node",
    help="Node name prefix (default: calimero-node)",
)
@click.option("--data-dir", help="Custom data directory for single node")
@click.option("--image", help="Custom Docker image to use")
@click.option(
    "--force-pull",
    is_flag=True,
    help="Force pull the Docker image even if it exists locally",
)
@click.option(
    "--auth-service",
    is_flag=True,
    help="Enable authentication service with Traefik proxy",
)
@click.option(
    "--auth-image",
    help="Custom Docker image for the auth service (default: ghcr.io/calimero-network/mero-auth:edge)",
)
@click.option(
    "--auth-use-cached",
    is_flag=True,
    help="Use cached auth frontend instead of fetching fresh (disables CALIMERO_AUTH_FRONTEND_FETCH)",
)
@click.option(
    "--webui-use-cached",
    is_flag=True,
    help="Use cached WebUI frontend instead of fetching fresh (disables CALIMERO_WEBUI_FETCH)",
)
@click.option(
    "--log-level",
    default="debug",
    help="Set the RUST_LOG level for Calimero nodes (default: debug). Supports complex patterns like 'info,module::path=debug'",
)
def run(
    count,
    base_port,
    base_rpc_port,
    chain_id,
    prefix,
    data_dir,
    image,
    force_pull,
    auth_service,
    auth_image,
    auth_use_cached,
    webui_use_cached,
    log_level,
):
    """Run Calimero node(s) in Docker containers."""
    calimero_manager = CalimeroManager()

    # Handle force pull if specified
    if force_pull and image:
        console.print(f"[yellow]Force pulling image: {image}[/yellow]")
        if not calimero_manager.force_pull_image(image):
            console.print(f"[red]Failed to force pull image: {image}[/red]")
            sys.exit(1)

    # Convert port parameters to integers if provided
    if base_port is not None:
        base_port = validate_port(base_port, "base port")

    if base_rpc_port is not None:
        base_rpc_port = validate_port(base_rpc_port, "base RPC port")

    if count == 1 and data_dir:
        # Single node with custom data directory
        node_name = f"{prefix}-1"
        success = calimero_manager.run_node(
            node_name,
            base_port,
            base_rpc_port,
            chain_id,
            data_dir,
            image,
            auth_service,
            auth_image,
            auth_use_cached,
            webui_use_cached,
            log_level,
        )
        sys.exit(0 if success else 1)
    else:
        # Multiple nodes or single node with default settings
        success = calimero_manager.run_multiple_nodes(
            count,
            base_port,
            base_rpc_port,
            chain_id,
            prefix,
            image,
            auth_service,
            auth_image,
            auth_use_cached,
            webui_use_cached,
            log_level,
        )
        sys.exit(0 if success else 1)
