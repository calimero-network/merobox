"""
Main bootstrap command - CLI interface for workflow execution.
"""

import click
import asyncio
import sys
from .executor import WorkflowExecutor
from .config import load_workflow_config, create_sample_workflow_config

@click.command()
@click.argument('config_file', type=click.Path(exists=True), required=False)
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.option('--create-sample', is_flag=True, help='Create a sample workflow configuration file')
def bootstrap(config_file, verbose, create_sample):
    """Execute a Calimero workflow from a YAML configuration file."""
    
    # Handle create-sample flag
    if create_sample:
        create_sample_workflow_config()
        return
    
    # Require config_file if not creating sample
    if not config_file:
        console.print("[red]Error: CONFIG_FILE is required unless using --create-sample[/red]")
        sys.exit(1)
    
    try:
        # Load configuration
        config = load_workflow_config(config_file)
        
        # Create and execute workflow
        from ..manager import CalimeroManager
        manager = CalimeroManager()
        executor = WorkflowExecutor(config, manager)
        
        # Execute workflow
        success = asyncio.run(executor.execute_workflow())
        
        if success:
            console.print("\n[bold green]🎉 Workflow completed successfully![/bold green]")
            if verbose and executor.workflow_results:
                console.print("\n[bold]Workflow Results:[/bold]")
                for key, value in executor.workflow_results.items():
                    console.print(f"  {key}: {value}")
        else:
            console.print("\n[bold red]❌ Workflow failed![/bold red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[red]Failed to execute workflow: {str(e)}[/red]")
        sys.exit(1)

# Import console for use in this module
from ..utils import console
