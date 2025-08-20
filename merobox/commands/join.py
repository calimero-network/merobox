"""
Join command - Join Calimero contexts using invitations via admin API.
"""

import click
import asyncio
import sys
from typing import Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich import box
from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import (
    get_node_rpc_url,
    console
)

async def join_context_via_admin_api(rpc_url: str, context_id: str, invitee_id: str, invitation_data: str) -> dict:
    """Join a context using an invitation via the admin API."""
    try:
        # Import the admin client
        from calimero import AdminClient
        
        # Create admin client and join context
        admin_client = AdminClient(rpc_url)
        result = await admin_client.join_context(context_id, invitee_id, invitation_data)
        
        # Add endpoint and payload format info for compatibility
        if result['success']:
            result['endpoint'] = f"{rpc_url}/admin-api/contexts/join"
            result['payload_format'] = 0  # Standard format
        
        return result
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

@click.group()
def join():
    """Join Calimero contexts using invitations."""
    pass

@join.command()
@click.option('--node', '-n', required=True, help='Node name to join context on')
@click.option('--context-id', required=True, help='Context ID to join')
@click.option('--invitee-id', required=True, help='Public key of the identity joining the context')
@click.option('--invitation', required=True, help='Invitation data/token to join the context')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def context(node, context_id, invitee_id, invitation, verbose):
    """Join a context using an invitation."""
    manager = CalimeroManager()
    
    # Get admin API URL and run join
    admin_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Joining context {context_id} on node {node} as {invitee_id} via {admin_url}[/blue]")
    
    result = asyncio.run(join_context_via_admin_api(admin_url, context_id, invitee_id, invitation))
    
    # Show which endpoint was used if successful
    if result['success'] and 'endpoint' in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")
    
    if result['success']:
        response_data = result.get('data', {})
        
        console.print(f"\n[green]✓ Successfully joined context![/green]")
        
        # Create table
        table = Table(title="Context Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Context ID", context_id)
        table.add_row("Invitee ID", invitee_id)
        table.add_row("Node", node)
        table.add_row("Payload Format", str(result.get('payload_format', 'N/A')))
        
        console.print(table)
        
        if verbose:
            console.print(f"\n[bold]Full response:[/bold]")
            console.print(f"{result}")
            
    else:
        console.print(f"\n[red]✗ Failed to join context[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        
        # Show detailed error information if available
        if 'errors' in result:
            console.print(f"\n[yellow]Detailed errors:[/yellow]")
            for error in result['errors']:
                console.print(f"[red]  {error}[/red]")
        
        if verbose:
            console.print(f"\n[bold]Full response:[/bold]")
            console.print(f"{result}")
        
        sys.exit(1)

if __name__ == '__main__':
    join()
