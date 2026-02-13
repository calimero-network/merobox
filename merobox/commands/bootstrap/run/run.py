"""
Workflow execution runner.

This module handles the execution of Calimero workflows including:
- Loading and validating configuration
- Creating workflow executor
- Running the workflow
- Handling results and errors
"""

import asyncio
import os
from typing import Any, Optional

from merobox.commands.bootstrap.config import load_workflow_config
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.utils import console


def merge_remote_nodes_config(
    yaml_config: dict[str, Any],
    cli_remote_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge CLI-provided remote nodes with YAML config.

    CLI options take precedence over YAML config.

    Args:
        yaml_config: The loaded workflow YAML configuration.
        cli_remote_nodes: Remote nodes from CLI options.

    Returns:
        Updated config with merged remote_nodes.
    """
    if not cli_remote_nodes:
        return yaml_config

    # Get existing remote_nodes from YAML or create empty dict
    existing_remote_nodes = yaml_config.get("remote_nodes", {})

    # Merge: CLI takes precedence
    # Create a deep copy of existing nodes to avoid mutating the original yaml_config
    merged_remote_nodes = {
        name: {**node_config} for name, node_config in existing_remote_nodes.items()
    }

    for name, cli_config in cli_remote_nodes.items():
        if name in merged_remote_nodes:
            # Merge individual node config, CLI takes precedence
            existing_node = merged_remote_nodes[name]
            # Update URL if provided
            if "url" in cli_config:
                existing_node["url"] = cli_config["url"]
            # Update auth if provided (and not just default 'none')
            cli_auth = cli_config.get("auth", {})
            if cli_auth.get("method") and cli_auth["method"] != "none":
                existing_node["auth"] = cli_auth
            elif cli_auth.get("method") == "none" and "auth" not in existing_node:
                existing_node["auth"] = cli_auth
        else:
            # New node from CLI
            merged_remote_nodes[name] = cli_config

    yaml_config["remote_nodes"] = merged_remote_nodes

    # Log merged remote nodes for visibility
    if merged_remote_nodes:
        console.print(
            f"[cyan]Remote nodes configured: {', '.join(merged_remote_nodes.keys())}[/cyan]"
        )

    return yaml_config


async def run_workflow(
    config_file: str,
    verbose: bool = False,
    image: Optional[str] = None,
    auth_service: bool = False,
    auth_image: Optional[str] = None,
    auth_use_cached: bool = False,
    webui_use_cached: bool = False,
    log_level: str = "debug",
    rust_backtrace: str = "0",
    no_docker: bool = False,
    binary_path: Optional[str] = None,
    e2e_mode: bool = False,
    enable_relayer: Optional[bool] = None,
    contracts_dir: Optional[str] = None,
    cli_remote_nodes: Optional[dict[str, dict[str, Any]]] = None,
    auth_mode: Optional[str] = None,
    auth_username: Optional[str] = None,
    auth_password: Optional[str] = None,
) -> bool:
    """
    Execute a Calimero workflow from a YAML configuration file.

    Args:
        config_file: Path to the workflow configuration file
        verbose: Whether to enable verbose output
        auth_service: Whether to enable authentication service integration
        cli_remote_nodes: Remote nodes config from CLI options (--remote-node/--remote-auth)
        auth_mode: Authentication mode for merod (binary mode only)
        auth_username: Username for embedded auth authentication
        auth_password: Password for embedded auth authentication

    Returns:
        True if workflow completed successfully, False otherwise
    """
    try:
        # Load configuration
        config = load_workflow_config(config_file)
        workflow_dir = os.path.dirname(os.path.abspath(config_file))

        # Merge CLI-provided remote nodes with YAML config
        config = merge_remote_nodes_config(config, cli_remote_nodes or {})

        # Allow workflow YAML to opt into no-docker mode
        yaml_no_docker = bool(config.get("no_docker", False))
        yaml_binary_path = config.get("binary_path")

        # CLI flag takes precedence, otherwise fall back to YAML
        effective_no_docker = no_docker or yaml_no_docker
        effective_binary_path = binary_path or yaml_binary_path

        # Determine effective auth_mode (CLI takes precedence over YAML)
        yaml_auth_mode = config.get("auth_mode")
        effective_auth_mode = auth_mode or yaml_auth_mode

        # Validate auth_mode configuration
        if effective_auth_mode:
            if not effective_no_docker:
                console.print(
                    "[red]auth_mode is only supported with --no-docker (binary mode) or no_docker: true in workflow config. "
                    "For Docker mode, use --auth-service instead.[/red]"
                )
                return False

            if effective_auth_mode == "embedded" and not (
                auth_username and auth_password
            ):
                console.print(
                    "[red]When using auth_mode=embedded (from CLI or workflow config), you must provide --auth-username and --auth-password "
                    "for workflow authentication.[/red]"
                )
                return False

        # Check if this is a remote-only workflow (no local nodes)
        # Local nodes are defined in 'nodes' config key
        has_local_nodes = "nodes" in config and config.get("nodes")
        has_remote_nodes = "remote_nodes" in config and config.get("remote_nodes")
        is_remote_only = has_remote_nodes and not has_local_nodes

        # Create and execute workflow
        # Choose manager implementation based on mode
        if is_remote_only:
            # Remote-only mode: no Docker or binary manager needed
            manager = None
            # Auth service and mock relayer don't apply to remote-only
            auth_service = False
        elif effective_no_docker:
            from merobox.commands.binary_manager import BinaryManager

            manager = BinaryManager(binary_path=effective_binary_path)
            # When running in binary mode, auth_service is not supported
            auth_service = False
        else:
            from merobox.commands.manager import DockerManager

            manager = DockerManager()

        # Debug: show incoming log level from CLI/defaults
        try:
            from merobox.commands.utils import console as _console

            _console.print(
                f"[cyan]run_workflow: incoming log_level='{log_level}'[/cyan]"
            )
            _console.print(
                f"[cyan]run_workflow: incoming rust_backtrace='{rust_backtrace}'[/cyan]"
            )
        except Exception:
            pass

        # enable_relayer=True => use testnet/relayer (near_devnet=False); enable_relayer=False => sandbox (near_devnet=True); None => defer to YAML.
        near_devnet = None if enable_relayer is None else (not enable_relayer)

        executor = WorkflowExecutor(
            config,
            manager,
            image,
            auth_service,
            auth_image,
            auth_use_cached,
            webui_use_cached,
            log_level,
            rust_backtrace,
            e2e_mode,
            workflow_dir=workflow_dir,
            near_devnet=near_devnet,
            contracts_dir=contracts_dir,
            auth_mode=effective_auth_mode,
            auth_username=auth_username,
            auth_password=auth_password,
        )

        # Execute workflow
        success = await executor.execute_workflow()

        if success:
            console.print(
                "\n[bold green]🎉 Workflow completed successfully![/bold green]"
            )
            if verbose and executor.workflow_results:
                console.print("\n[bold]Workflow Results:[/bold]")
                for key, value in executor.workflow_results.items():
                    console.print(f"  {key}: {value}")
        else:
            console.print("\n[bold red]❌ Workflow failed![/bold red]")

        return success

    except Exception as e:
        console.print(f"[red]Failed to execute workflow: {str(e)}[/red]")
        return False


def run_workflow_sync(
    config_file: str,
    verbose: bool = False,
    image: Optional[str] = None,
    auth_service: bool = False,
    auth_image: Optional[str] = None,
    auth_use_cached: bool = False,
    webui_use_cached: bool = False,
    log_level: str = "debug",
    rust_backtrace: str = "0",
    no_docker: bool = False,
    binary_path: Optional[str] = None,
    e2e_mode: bool = False,
    enable_relayer: Optional[bool] = None,
    contracts_dir: Optional[str] = None,
    cli_remote_nodes: Optional[dict[str, dict[str, Any]]] = None,
    auth_mode: Optional[str] = None,
    auth_username: Optional[str] = None,
    auth_password: Optional[str] = None,
) -> bool:
    """
    Synchronous wrapper for workflow execution.

    Args:
        config_file: Path to the workflow configuration file
        verbose: Whether to enable verbose output
        auth_service: Whether to enable authentication service integration
        cli_remote_nodes: Remote nodes config from CLI options (--remote-node/--remote-auth)
        auth_mode: Authentication mode for merod (binary mode only)
        auth_username: Username for embedded auth authentication
        auth_password: Password for embedded auth authentication

    Returns:
        True if workflow completed successfully, False otherwise
    """
    return asyncio.run(
        run_workflow(
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
    )
