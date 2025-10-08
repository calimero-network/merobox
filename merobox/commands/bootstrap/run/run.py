"""
Workflow execution runner.

This module handles the execution of Calimero workflows including:
- Loading and validating configuration
- Creating workflow executor
- Running the workflow
- Handling results and errors
"""

import asyncio
from typing import Optional

from merobox.commands.bootstrap.config import load_workflow_config
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.utils import console


async def run_workflow(
    config_file: str,
    verbose: bool = False,
    image: Optional[str] = None,
    auth_service: bool = False,
    auth_image: str = None,
    auth_use_cached: bool = False,
    webui_use_cached: bool = False,
    log_level: str = "debug",
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

        # Create and execute workflow
        from merobox.commands.manager import CalimeroManager

        manager = CalimeroManager()

        # Debug: show incoming log level from CLI/defaults
        try:
            from merobox.commands.utils import console as _console
            _console.print(f"[cyan]run_workflow: incoming log_level='{log_level}'[/cyan]")
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
    auth_image: str = None,
    auth_use_cached: bool = False,
    webui_use_cached: bool = False,
    log_level: str = "debug",
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
        )
    )
