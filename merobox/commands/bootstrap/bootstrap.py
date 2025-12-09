"""
Bootstrap command - CLI interface for workflow execution and validation.

This module provides the main bootstrap command with three subcommands:
1. run - Execute a workflow from YAML configuration
2. validate - Validate workflow configuration without execution
3. create-sample - Create a sample workflow configuration file

The bootstrap command is designed as a Click command group to provide
a clean, organized interface for workflow management.
"""

import sys

import click

from merobox.commands.bootstrap.config import (
    create_sample_workflow_config,
    load_workflow_config,
)
from merobox.commands.bootstrap.run import run_workflow_sync
from merobox.commands.bootstrap.validate import validate_workflow_config
from merobox.commands.utils import console


@click.group()
def bootstrap():
    """
    Execute and validate Calimero workflows from YAML configuration files.

    This command provides three main operations:
    • run: Execute a complete workflow
    • validate: Check workflow configuration for errors
    • create-sample: Generate a sample workflow file
    """
    pass


@bootstrap.command()
@click.argument("config_file", type=click.Path(exists=True), required=True)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option(
    "--image",
    help="Custom Docker image to use for Calimero nodes (overrides workflow config)",
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
@click.option(
    "--rust-backtrace",
    default="0",
    help="Set the RUST_BACKTRACE level for Calimero nodes (default: 0)",
)
@click.option(
    "--no-docker",
    is_flag=True,
    help="Run nodes as native binaries (merod) instead of Docker containers",
)
@click.option(
    "--binary-path",
    help="Set custom path to merod binary (used with --no-docker). Defaults to searching PATH and common locations (/usr/local/bin, /usr/bin, ~/bin).",
)
@click.option(
    "--mock-relayer",
    is_flag=True,
    help="Start a local mock relayer (Docker only) and wire nodes to it",
)
@click.option(
    "--e2e-mode",
    is_flag=True,
    help="Enable e2e test mode with aggressive sync settings and test isolation (disables bootstrap nodes, uses unique rendezvous namespaces)",
)
@click.option(
    "--near-devnet",
    is_flag=True,
    help="Spin up a local NEAR sandbox and configure nodes to use it. Requires --contracts-dir.",
)
@click.option(
    "--contracts-dir",
    type=click.Path(exists=True),
    help="Directory containing context_config_near.wasm and context_proxy_near.wasm",
)
def run(
    config_file,
    verbose,
    image,
    auth_service,
    auth_image,
    auth_use_cached,
    webui_use_cached,
    log_level,
    rust_backtrace,
    no_docker,
    binary_path,
    mock_relayer,
    e2e_mode,
    near_devnet,
    contracts_dir,
):
    """
    Execute a Calimero workflow from a YAML configuration file.

    This command will:
    1. Load and validate the workflow configuration
    2. Start/restart Calimero nodes as needed
    3. Execute each step in sequence
    4. Handle dynamic variable resolution
    5. Export results and captured values
    """

    success = run_workflow_sync(
        config_file,
        verbose,
        image=image,
        auth_service=auth_service,
        auth_image=auth_image,
        auth_use_cached=auth_use_cached,
        webui_use_cached=webui_use_cached,
        log_level=log_level,
        rust_backtrace=rust_backtrace,
        no_docker=no_docker,
        binary_path=binary_path,
        mock_relayer=mock_relayer,
        e2e_mode=e2e_mode,
        near_devnet=near_devnet,
        contracts_dir=contracts_dir,
    )
    if not success:
        sys.exit(1)


@bootstrap.command()
@click.argument("config_file", type=click.Path(exists=True), required=True)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def validate(config_file, verbose):
    """
    Validate a Calimero workflow YAML configuration file.

    This command performs comprehensive validation:
    • Checks required fields and structure
    • Validates step configurations
    • Ensures proper field types
    • Reports all validation errors

    Use this before running workflows to catch configuration issues early.
    """
    try:
        # Load configuration with validation-only mode
        config = load_workflow_config(config_file, validate_only=True)

        # Validate the workflow configuration
        validation_result = validate_workflow_config(config, verbose)

        if validation_result["valid"]:
            console.print(
                "\n[bold green]✅ Workflow configuration is valid![/bold green]"
            )
            if verbose:
                console.print("\n[bold]Configuration Summary:[/bold]")
                console.print(f"  Name: {config.get('name', 'Unnamed')}")
                console.print(f"  Steps: {len(config.get('steps', []))}")
                nodes_config = config.get("nodes", {})
                if isinstance(nodes_config, dict):
                    console.print(f"  Nodes: {nodes_config.get('count', 'N/A')}")
                    console.print(f"  Chain ID: {nodes_config.get('chain_id', 'N/A')}")
                else:
                    console.print("  Nodes: N/A")
                    console.print("  Chain ID: N/A")
        else:
            console.print(
                "\n[bold red]❌ Workflow configuration validation failed![/bold red]"
            )
            for error in validation_result["errors"]:
                console.print(f"  [red]• {error}[/red]")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Failed to validate workflow: {str(e)}[/red]")
        sys.exit(1)


@bootstrap.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def create_sample(verbose):
    """
    Create a sample workflow configuration file.

    This generates a complete, working example workflow that demonstrates:
    • Basic node configuration
    • Common workflow steps
    • Dynamic variable usage
    • Output configuration

    The sample file can be used as a starting point for custom workflows.
    """
    create_sample_workflow_config()
    if verbose:
        console.print(
            "\n[green]Sample workflow configuration created successfully![/green]"
        )
