"""
Main bootstrap command - CLI interface for workflow execution.
"""

import click
import asyncio
import sys
from .executor import WorkflowExecutor
from .config import load_workflow_config, create_sample_workflow_config

@click.group()
def bootstrap():
    """Bootstrap command - Automate Calimero node workflows using YAML configuration files."""
    pass

@bootstrap.command()
@click.argument('config_file', type=click.Path(exists=True))
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
def run(config_file, verbose):
    """Run a workflow from a YAML configuration file."""
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

@bootstrap.command()
def create_sample():
    """Create a sample workflow configuration file."""
    create_sample_workflow_config()

@bootstrap.command()
@click.argument('config_file', type=click.Path(exists=True))
def validate(config_file):
    """Validate a workflow configuration file."""
    try:
        config = load_workflow_config(config_file)
        console.print(f"[green]✓ Configuration file '{config_file}' is valid[/green]")
        console.print(f"[blue]Workflow: {config.get('name', 'Unnamed')}[/blue]")
        console.print(f"[blue]Steps: {len(config.get('steps', []))}[/blue]")
    except Exception as e:
        console.print(f"[red]❌ Configuration file is invalid: {str(e)}[/red]")
        sys.exit(1)

# Import console for use in this module
from ..utils import console
