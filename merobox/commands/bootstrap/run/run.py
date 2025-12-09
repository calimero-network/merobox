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
from typing import Optional

from merobox.commands.bootstrap.config import load_workflow_config
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.utils import console


async def run_workflow(
    config_file: str,
    verbose: bool = False,
    image: Optional[str] = None,
    auth_service: bool = False,
    auth_image: [str] = None,
    auth_use_cached: bool = False,
    webui_use_cached: bool = False,
    log_level: str = "debug",
    rust_backtrace: str = "0",
    no_docker: bool = False,
    binary_path: Optional[str] = None,
    mock_relayer: bool = False,
    e2e_mode: bool = False,
    near_devnet: bool = False,
    contracts_dir: Optional[str] = None,
) -> bool:
    """
    Execute a Calimero workflow from a YAML configuration file.

    Args:
        config_file: Path to the workflow configuration file
        verbose: Whether to enable verbose output
        auth_service: Whether to enable authentication service integration

    Returns:
        True if workflow completed successfully, False otherwise
    """
    try:
        # Load configuration
        config = load_workflow_config(config_file)
        workflow_dir = os.path.dirname(os.path.abspath(config_file))

        # Allow workflow YAML to opt into no-docker mode
        yaml_no_docker = bool(config.get("no_docker", False))
        yaml_binary_path = config.get("binary_path")

        # CLI flag takes precedence, otherwise fall back to YAML
        effective_no_docker = no_docker or yaml_no_docker
        effective_binary_path = binary_path or yaml_binary_path

        if mock_relayer and effective_no_docker:
            console.print(
                "[red]--mock-relayer requires Docker mode; remove --no-docker or yaml no_docker flag[/red]"
            )
            return False

        # Create and execute workflow
        # Choose manager implementation based on effective_no_docker
        if effective_no_docker:
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
            if mock_relayer:
                _console.print("[cyan]run_workflow: mock relayer requested[/cyan]")
        except Exception:
            pass

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
            mock_relayer,
            e2e_mode,
            workflow_dir=workflow_dir,
            near_devnet=near_devnet,
            contracts_dir=contracts_dir,
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
    mock_relayer: bool = False,
    e2e_mode: bool = False,
    near_devnet: bool = False,
    contracts_dir: Optional[str] = None,
) -> bool:
    """
    Synchronous wrapper for workflow execution.

    Args:
        config_file: Path to the workflow configuration file
        verbose: Whether to enable verbose output
        auth_service: Whether to enable authentication service integration

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
            mock_relayer=mock_relayer,
            e2e_mode=e2e_mode,
            near_devnet=near_devnet,
            contracts_dir=contracts_dir,
        )
    )
