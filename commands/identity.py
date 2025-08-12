"""
Identity command - List and generate identities for Calimero contexts.
"""

import click
import sys
from rich.console import Console
from rich.table import Table
from rich import box
from .manager import CalimeroManager
from .utils import (
    get_node_rpc_url, 
    check_node_running, 
    run_async_function,
    extract_nested_data,
    console
)

def extract_identities_from_response(response_data: dict) -> list:
    """Extract identities from different possible response structures."""
    identities_data = extract_nested_data(response_data, 'identities')
    return identities_data if identities_data else []

def create_identity_table(identities_data: list, context_id: str) -> Table:
    """Create a table to display identities."""
    table = Table(title=f"Identities for Context {context_id}", box=box.ROUNDED)
    table.add_column("Identity ID", style="cyan")
    table.add_column("Context ID", style="cyan")
    table.add_column("Public Key", style="yellow")
    table.add_column("Status", style="blue")
    
    for identity_info in identities_data:
        if isinstance(identity_info, dict):
            # Handle case where identity_info is a dictionary
            table.add_row(
                identity_info.get('id', 'Unknown'),
                identity_info.get('contextId', 'Unknown'),
                identity_info.get('publicKey', 'Unknown'),
                identity_info.get('status', 'Unknown')
            )
        else:
            # Handle case where identity_info is a string (just the ID)
            table.add_row(
                str(identity_info), 
                context_id,
                'N/A', 
                'Active'
            )
    
    return table

async def list_identities_via_api(rpc_url: str, context_id: str) -> dict:
    """List identities for a specific context using the admin API."""
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            endpoint = f"{rpc_url}/admin-api/contexts/{context_id}/identities"
            headers = {'Content-Type': 'application/json'}
            
            async with session.get(endpoint, headers=headers, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    return {'success': True, 'data': result}
                else:
                    error_text = await response.text()
                    return {'success': False, 'error': f"HTTP {response.status}: {error_text}"}
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def generate_identity_via_api(rpc_url: str) -> dict:
    """Generate a new identity using the admin API as per Rust implementation."""
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            # Based on Rust implementation: admin-api/identity/context with no parameters
            endpoint = f"{rpc_url}/admin-api/identity/context"
            
            headers = {'Content-Type': 'application/json'}
            
            # No payload needed as per Rust implementation
            async with session.post(endpoint, json=None, headers=headers, timeout=30) as response:
                if response.status in [200, 201]:
                    result = await response.json()
                    return {'success': True, 'data': result, 'endpoint': endpoint}
                else:
                    error_text = await response.text()
                    return {'success': False, 'error': f"HTTP {response.status}: {error_text}"}
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

@click.group()
def identity():
    """Manage Calimero identities for contexts."""
    pass

@identity.command()
@click.option('--node', '-n', required=True, help='Node name to list identities from')
@click.option('--context-id', required=True, help='Context ID to list identities for')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def list_identities(node, context_id, verbose):
    """List identities for a specific context on a node."""
    manager = CalimeroManager()
    
    # Check if node is running
    check_node_running(node, manager)
    
    # Get admin API URL and run listing
    admin_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing identities for context {context_id} on node {node} via {admin_url}[/blue]")
    
    result = run_async_function(list_identities_via_api, admin_url, context_id)
    
    if result['success']:
        response_data = result.get('data', {})
        identities_data = extract_identities_from_response(response_data)
        
        if not identities_data:
            console.print(f"\n[yellow]No identities found for context {context_id} on node {node}[/yellow]")
            if verbose:
                console.print(f"\n[bold]Response structure:[/bold]")
                console.print(f"{result}")
            return
        
        console.print(f"\n[green]Found {len(identities_data)} identity(ies):[/green]")
        
        # Create and display table
        table = create_identity_table(identities_data, context_id)
        console.print(table)
        
        if verbose:
            console.print(f"\n[bold]Full response:[/bold]")
            console.print(f"{result}")
            
    else:
        console.print(f"\n[red]✗ Failed to list identities[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)

@identity.command()
@click.option('--node', '-n', required=True, help='Node name to generate identity on')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def generate(node, verbose=False):
    """Generate a new identity using the admin API."""
    manager = CalimeroManager()
    
    # Check if node is running
    check_node_running(node, manager)
    
    # Get admin API URL and run generation
    admin_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Generating new identity on node {node} via {admin_url}[/blue]")
    
    result = run_async_function(generate_identity_via_api, admin_url)
    
    # Show which endpoint was used if successful
    if result['success'] and 'endpoint' in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")
    
    if result['success']:
        response_data = result.get('data', {})
        
        # Extract identity information from response
        identity_data = response_data.get('identity') or response_data.get('data') or response_data
        
        if identity_data:
            console.print(f"\n[green]✓ Identity generated successfully![/green]")
            
            # Create table
            table = Table(title="New Identity Details", box=box.ROUNDED)
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")
            
            if 'id' in identity_data:
                table.add_row("Identity ID", identity_data['id'])
            if 'publicKey' in identity_data:
                table.add_row("Public Key", identity_data['publicKey'])

            
            console.print(table)
        else:
            console.print(f"\n[green]✓ Identity generated successfully![/green]")
            console.print(f"[yellow]Response: {response_data}[/yellow]")
        
        if verbose:
            console.print(f"\n[bold]Full response:[/bold]")
            console.print(f"{result}")
            
    else:
        console.print(f"\n[red]✗ Failed to generate identity[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)

async def invite_identity_via_api(rpc_url: str, context_id: str, granter_id: str, grantee_id: str, capability: str) -> dict:
    """Invite an identity to a context by granting capabilities using the admin API."""
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            # Based on Rust implementation: use the correct grant endpoint
            endpoint = f"{rpc_url}/admin-api/contexts/{context_id}/capabilities/grant"
            
            # Create payload for granting capabilities based on Rust implementation
            # The Rust code shows: vec![(grantee_id, self.capability.into())]
            # The API expects a vector of tuples, so we use a list of tuples
            # Each tuple should have exactly 2 elements: [grantee_id, capability]
            # Try different payload structures to match the API expectations
            payload = [
                [grantee_id, capability]
            ]
            
            # Alternative payload formats to try if the first one fails
            # Map common capability names to the expected enum values
            capability_mapping = {
                'member': 'ManageMembers',
                'manage': 'ManageMembers', 
                'admin': 'ManageApplication',
                'proxy': 'Proxy'
            }
            
            mapped_capability = capability_mapping.get(capability, capability)
            
            alternative_payloads = [
                {"signer_id": granter_id, "capabilities": [[grantee_id, mapped_capability]]},  # With signer_id and capabilities
                {"capabilities": [[grantee_id, mapped_capability]]},  # Just capabilities
                {"signer_id": granter_id, "capabilities": [grantee_id, mapped_capability]},  # Alternative format
                [grantee_id, mapped_capability],  # Simple tuple format
            ]
            
            headers = {'Content-Type': 'application/json'}
            
            # Try different payload formats
            errors = []
            for i, test_payload in enumerate(alternative_payloads):
                try:
                    async with session.post(endpoint, json=test_payload, headers=headers, timeout=30) as response:
                        if response.status in [200, 201]:
                            result = await response.json()
                            return {'success': True, 'data': result, 'endpoint': endpoint, 'payload_format': i}
                        else:
                            error_text = await response.text()
                            errors.append(f"Format {i}: HTTP {response.status} - {error_text}")
                            if response.status != 422:
                                # If it's not a 422, stop trying
                                return {'success': False, 'error': f"HTTP {response.status}: {error_text}", 'payload': test_payload, 'payload_format': i}
                except Exception as e:
                    errors.append(f"Format {i}: Exception - {str(e)}")
                    continue
            
            # If all payload formats failed, return detailed error
            return {'success': False, 'error': 'All payload formats failed', 'endpoint': endpoint, 'tried_payloads': alternative_payloads, 'errors': errors}
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

@identity.command()
@click.option('--node', '-n', required=True, help='Node name to invite identity on')
@click.option('--context-id', required=True, help='Context ID to invite identity to')
@click.option('--granter-id', required=True, help='Public key of the granter (inviter)')
@click.option('--grantee-id', required=True, help='Public key of the grantee (existing member)')
@click.option('--capability', default='member', help='Capability to grant (default: member)')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def invite(node, context_id, granter_id, grantee_id, capability, verbose):
    """Grant capabilities to an existing member of a context."""
    manager = CalimeroManager()
    
    # Check if node is running
    check_node_running(node, manager)
    
    # Get admin API URL and run invitation
    admin_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Granting {capability} capability to identity {grantee_id} in context {context_id} on node {node} via {admin_url}[/blue]")
    
    result = run_async_function(invite_identity_via_api, admin_url, context_id, granter_id, grantee_id, capability)
    
    # Show which endpoint was used if successful
    if result['success'] and 'endpoint' in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")
    
    if result['success']:
        response_data = result.get('data', {})
        
        console.print(f"\n[green]✓ Capability granted successfully![/green]")
        
        # Create table
        table = Table(title="Capability Grant Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Context ID", context_id)
        table.add_row("Granter ID", granter_id)
        table.add_row("Grantee ID", grantee_id)
        table.add_row("Capability", capability)
        
        console.print(table)
        
        if verbose:
            console.print(f"\n[bold]Full response:[/bold]")
            console.print(f"{result}")
            
    else:
        console.print(f"\n[red]✗ Failed to invite identity[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        
        # Show detailed error information if available
        if 'errors' in result:
            console.print(f"\n[yellow]Detailed errors:[/yellow]")
            for error in result['errors']:
                console.print(f"[red]  {error}[/red]")
        
        if 'tried_payloads' in result:
            console.print(f"\n[yellow]Tried payload formats:[/yellow]")
            for i, payload in enumerate(result['tried_payloads']):
                console.print(f"[dim]  Format {i}: {payload}[/dim]")
        
        # Provide helpful information for common errors
        if "unable to grant privileges to non-member" in result.get('error', ''):
            console.print(f"\n[yellow]Note: The grantee identity must already be a member of the context before granting capabilities.[/yellow]")
            console.print(f"[yellow]This command is for granting capabilities to existing members, not for adding new members.[/yellow]")
        
        sys.exit(1)

if __name__ == '__main__':
    identity()
