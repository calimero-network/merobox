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
from typing import Any

import click

from merobox.commands.bootstrap.config import (
    create_sample_workflow_config,
    load_workflow_config,
)
from merobox.commands.bootstrap.run import run_workflow_sync
from merobox.commands.bootstrap.validate import validate_workflow_config
from merobox.commands.utils import console


def parse_remote_nodes(remote_node_args: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Parse --remote-node arguments into a config dict.

    Args:
        remote_node_args: Tuple of "name=url" strings.

    Returns:
        Dict mapping node names to their config: {name: {"url": url, "auth": {...}}}
    """
    remote_nodes = {}
    for arg in remote_node_args:
        if "=" not in arg:
            console.print(
                f"[yellow]Warning: Invalid --remote-node format '{arg}', expected 'name=url'[/yellow]"
            )
            continue
        name, url = arg.split("=", 1)
        name = name.strip()
        url = url.strip()
        if not name or not url:
            console.print(
                f"[yellow]Warning: Invalid --remote-node '{arg}', name and url must not be empty[/yellow]"
            )
            continue
        remote_nodes[name] = {
            "url": url,
            "auth": {"method": "none"},  # Default to no auth
        }
    return remote_nodes


def parse_remote_auth(
    remote_auth_args: tuple[str, ...],
    remote_nodes: dict[str, dict[str, Any]],
    default_api_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse --remote-auth arguments and merge into remote_nodes config.

    Args:
        remote_auth_args: Tuple of "name=user:pass" or "name=apikey:KEY" strings.
        remote_nodes: Existing remote nodes config to update.
        default_api_key: Default API key to apply to nodes without explicit auth.

    Returns:
        Updated remote_nodes dict with auth configuration.
    """
    for arg in remote_auth_args:
        if "=" not in arg:
            console.print(
                f"[yellow]Warning: Invalid --remote-auth format '{arg}', expected 'name=user:pass' or 'name=apikey:KEY'[/yellow]"
            )
            continue
        name, auth_str = arg.split("=", 1)
        name = name.strip()
        auth_str = auth_str.strip()

        if not name:
            console.print(
                f"[yellow]Warning: Invalid --remote-auth '{arg}', node name must not be empty[/yellow]"
            )
            continue

        # Create node entry if it doesn't exist yet
        if name not in remote_nodes:
            console.print(
                f"[yellow]Warning: --remote-auth for '{name}' but no --remote-node defined for it. "
                f"Auth will be stored but node URL must be defined in workflow config.[/yellow]"
            )
            remote_nodes[name] = {"auth": {}}

        # Parse auth string - support both user:pass and apikey:KEY formats
        if auth_str.lower().startswith("apikey:"):
            api_key = auth_str[7:]  # Remove "apikey:" prefix
            remote_nodes[name]["auth"] = {
                "method": "api_key",
                "api_key": api_key,
            }
        elif ":" in auth_str:
            username, password = auth_str.split(":", 1)
            remote_nodes[name]["auth"] = {
                "method": "user_password",
                "username": username,
                "password": password,
            }
        else:
            console.print(
                f"[yellow]Warning: Invalid auth format for '{name}': '{auth_str}'. "
                f"Expected 'user:pass' or 'apikey:KEY'[/yellow]"
            )

    # Apply default API key to nodes without explicit auth
    if default_api_key:
        for name, config in remote_nodes.items():
            auth = config.get("auth", {})
            if auth.get("method") == "none" or not auth.get("method"):
                remote_nodes[name]["auth"] = {
                    "method": "api_key",
                    "api_key": default_api_key,
                }

    return remote_nodes


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
    "--e2e-mode",
    is_flag=True,
    help="Enable e2e test mode with aggressive sync settings and test isolation (disables bootstrap nodes, uses unique rendezvous namespaces)",
)
@click.option(
    "--enable-relayer",
    "enable_relayer",
    flag_value=True,
    default=None,
    help="Use the relayer/testnet for NEAR. If omitted, workflow YAML near_devnet is used (default: local sandbox).",
)
@click.option(
    "--no-enable-relayer",
    "enable_relayer",
    flag_value=False,
    help="Use local NEAR sandbox (explicit). Overrides workflow YAML near_devnet.",
)
@click.option(
    "--contracts-dir",
    type=click.Path(exists=True),
    default=None,
    help="Directory containing calimero_context_config_near.wasm and calimero_context_proxy_near.wasm. If omitted, contracts are downloaded automatically (unless --enable-relayer).",
)
@click.option(
    "--remote-node",
    multiple=True,
    metavar="NAME=URL",
    help="Register a remote node for this workflow run. Format: name=url (e.g., 'prod=https://node.example.com'). Can be specified multiple times.",
)
@click.option(
    "--remote-auth",
    multiple=True,
    metavar="NAME=AUTH",
    help="Set authentication for a remote node. Format: name=user:pass or name=apikey:KEY. Can be specified multiple times.",
)
@click.option(
    "--api-key",
    help="Default API key to use for remote nodes without explicit auth configuration.",
)
@click.option(
    "--auth-mode",
    type=click.Choice(["embedded", "proxy"], case_sensitive=False),
    default=None,
    help="Authentication mode for merod (binary mode only). 'embedded' enables built-in auth with JWT protection on all endpoints. Default is 'proxy' (no embedded auth).",
)
@click.option(
    "--auth-username",
    help="Username for embedded auth authentication. Required when --auth-mode=embedded for workflow execution.",
)
@click.option(
    "--auth-password",
    help="Password for embedded auth authentication. Required when --auth-mode=embedded for workflow execution.",
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
    e2e_mode,
    enable_relayer,
    contracts_dir,
    remote_node,
    remote_auth,
    api_key,
    auth_mode,
    auth_username,
    auth_password,
):
    """
    Execute a Calimero workflow from a YAML configuration file.

    This command will:
    1. Load and validate the workflow configuration
    2. Start/restart Calimero nodes as needed
    3. Execute each step in sequence
    4. Handle dynamic variable resolution
    5. Export results and captured values

    Remote nodes can be specified via --remote-node and --remote-auth flags:
    - --remote-node prod=https://node.example.com
    - --remote-auth prod=admin:password123
    - --remote-auth staging=apikey:sk-xxx

    These CLI-specified remote nodes are merged with any remote_nodes
    defined in the workflow YAML file, with CLI options taking precedence.
    """
    # Validate --auth-mode is only used with --no-docker (binary mode)
    if auth_mode and not no_docker:
        console.print(
            "[red]--auth-mode is only supported with --no-docker (binary mode). "
            "For Docker mode, use --auth-service instead.[/red]"
        )
        sys.exit(1)

    # Validate that auth credentials are provided when --auth-mode=embedded
    if auth_mode == "embedded" and not (auth_username and auth_password):
        console.print(
            "[red]When using --auth-mode=embedded, you must provide --auth-username and --auth-password "
            "for workflow authentication.[/red]"
        )
        sys.exit(1)

    # Parse remote node CLI options
    cli_remote_nodes = parse_remote_nodes(remote_node)
    cli_remote_nodes = parse_remote_auth(remote_auth, cli_remote_nodes, api_key)

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
        e2e_mode=e2e_mode,
        enable_relayer=enable_relayer,
        contracts_dir=contracts_dir,
        cli_remote_nodes=cli_remote_nodes,
        auth_mode=auth_mode,
        auth_username=auth_username,
        auth_password=auth_password,
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
