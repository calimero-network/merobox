"""
Context command - Create and manage Calimero contexts using JSON-RPC client.
"""

import click
import asyncio
import sys
import json
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn
from .manager import CalimeroManager

console = Console()

async def create_context_via_admin_api(
    rpc_url: str, 
    application_id: str
) -> dict:
    """Create a Calimero context using the admin API."""
    try:
        # Import the admin client
        from calimero import AdminClient
        
        # Create admin client and create context
        admin_client = AdminClient(rpc_url)
        result = await admin_client.create_context(application_id)
        
        return result
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def list_contexts_via_admin_api(rpc_url: str) -> dict:
    """List all Calimero contexts using the admin API."""
    try:
        # Import the admin client
        from calimero import AdminClient
        
        # Create admin client and list contexts
        admin_client = AdminClient(rpc_url)
        result = await admin_client.list_contexts()
        
        return result
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

async def get_context_via_admin_api(rpc_url: str, context_id: str) -> dict:
    """Get information about a specific Calimero context using the admin API."""
    try:
        # Import the admin client
        from calimero import AdminClient
        
        # Create admin client and get context
        admin_client = AdminClient(rpc_url)
        result = await admin_client.get_context(context_id)
        
        return result
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_node_rpc_url(node_name: str, manager: CalimeroManager) -> str:
    """Get the RPC URL for a specific node."""
    try:
        container = manager.client.containers.get(node_name)
        
        # Get port mappings from container attributes
        if container.attrs.get('NetworkSettings', {}).get('Ports'):
            port_mappings = container.attrs['NetworkSettings']['Ports']
            
            for container_port, host_bindings in port_mappings.items():
                if host_bindings and container_port == '2528/tcp':
                    for binding in host_bindings:
                        if 'HostPort' in binding:
                            host_port = binding['HostPort']
                            return f"http://localhost:{host_port}"
        
        # Fallback to default
        return "http://localhost:2528"
        
    except Exception:
        return "http://localhost:2528"

def validate_context_config(context_type: str, network: str, contract_id: str) -> tuple[bool, str]:
    """Validate the context configuration."""
    valid_types = ['ethereum', 'icp', 'near', 'starknet', 'stellar']
    valid_networks = {
        'ethereum': ['mainnet', 'sepolia', 'goerli', 'local'],
        'icp': ['mainnet', 'testnet', 'local'],
        'near': ['mainnet', 'testnet', 'local'],
        'starknet': ['mainnet', 'sepolia', 'goerli', 'local'],
        'stellar': ['mainnet', 'testnet', 'local']
    }
    
    if context_type not in valid_types:
        return False, f"Invalid context type. Must be one of: {', '.join(valid_types)}"
    
    if network not in valid_networks.get(context_type, []):
        return False, f"Invalid network for {context_type}. Must be one of: {', '.join(valid_networks.get(context_type, []))}"
    
    if not contract_id:
        return False, "Contract ID is required"
    
    return True, ""

@click.group()
def context():
    """Manage Calimero contexts for different blockchain networks."""
    pass

@context.command()
@click.option('--node', '-n', required=True, help='Node name to create the context on')
@click.option('--application-id', required=True, help='Application ID to create context for')
@click.option('--timeout', default=30, help='Timeout in seconds (default: 30)')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def create(node, application_id, timeout, verbose):
    """Create a new Calimero context."""
    manager = CalimeroManager()
    
    try:
        # Check if node is running
        try:
            container = manager.client.containers.get(node)
            if container.status != 'running':
                console.print(f"[red]Node {node} is not running[/red]")
                sys.exit(1)
        except Exception:
            console.print(f"[red]Node {node} not found[/red]")
            sys.exit(1)
        
        # Get admin API URL
        admin_url = get_node_rpc_url(node, manager)
        console.print(f"[blue]Creating context on node {node} via {admin_url}[/blue]")
        
        # Show context details
        console.print(f"[blue]Application ID: {application_id}[/blue]")
        
        # Run context creation with progress indicator
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Creating context...", total=None)
            
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Create context creation task
                create_task = loop.create_task(
                    create_context_via_admin_api(admin_url, application_id)
                )
                
                # Wait for result with timeout
                result = loop.run_until_complete(
                    asyncio.wait_for(create_task, timeout=timeout)
                )
                
                loop.close()
                
                progress.update(task, description="Context creation completed")
                
                if result['success']:
                    # Extract context ID
                    context_data = result.get('data', {})
                    context_id = context_data.get('data', {}).get('context_id', 'Unknown')
                    
                    console.print(f"\n[green]✓ Context created successfully![/green]")
                    console.print(f"[green]Context ID: {context_id}[/green]")
                    
                    if verbose:
                        console.print(f"\n[bold]Creation response:[/bold]")
                        console.print(f"{result}")
                    
                else:
                    console.print(f"\n[red]✗ Context creation failed[/red]")
                    console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
                    sys.exit(1)
                    
            except asyncio.TimeoutError:
                progress.update(task, description="Context creation timed out")
                console.print(f"\n[red]✗ Context creation timed out after {timeout} seconds[/red]")
                sys.exit(1)
            except Exception as e:
                progress.update(task, description="Context creation failed")
                console.print(f"\n[red]✗ Context creation failed: {str(e)}[/red]")
                sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]Failed to create context: {str(e)}[/red]")
        sys.exit(1)

@context.command()
@click.option('--node', '-n', required=True, help='Node name to list contexts from')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def list_contexts(node, verbose):
    """List all Calimero contexts on a node."""
    manager = CalimeroManager()
    
    try:
        # Check if node is running
        try:
            container = manager.client.containers.get(node)
            if container.status != 'running':
                console.print(f"[red]Node {node} is not running[/red]")
                sys.exit(1)
        except Exception:
            console.print(f"[red]Node {node} not found[/red]")
            sys.exit(1)
        
        # Get admin API URL
        admin_url = get_node_rpc_url(node, manager)
        console.print(f"[blue]Listing contexts on node {node} via {admin_url}[/blue]")
        
        # Run context listing
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(list_contexts_via_admin_api(admin_url))
            loop.close()
            
            if result['success']:
                contexts_data = result.get('data', {}).get('data', {}).get('contexts', [])
                
                if not contexts_data:
                    console.print(f"\n[yellow]No contexts found on node {node}[/yellow]")
                    return
                
                console.print(f"\n[green]Found {len(contexts_data)} context(s):[/green]")
                
                # Create table
                table = Table(title=f"Calimero Contexts on {node}", box=box.ROUNDED)
                table.add_column("Context ID", style="cyan")
                table.add_column("Application ID", style="green")
                table.add_column("Root Hash", style="yellow")
                
                for context_info in contexts_data:
                    table.add_row(
                        context_info.get('id', 'Unknown'),
                        context_info.get('applicationId', 'Unknown'),
                        context_info.get('rootHash', 'Unknown')
                    )
                
                console.print(table)
                
                if verbose:
                    console.print(f"\n[bold]Full response:[/bold]")
                    console.print(f"{result}")
                    
            else:
                console.print(f"\n[red]✗ Failed to list contexts[/red]")
                console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
                sys.exit(1)
                
        except Exception as e:
            console.print(f"\n[red]✗ Failed to list contexts: {str(e)}[/red]")
            sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]Failed to list contexts: {str(e)}[/red]")
        sys.exit(1)

@context.command()
@click.option('--node', '-n', required=True, help='Node name to get context from')
@click.option('--context-id', required=True, help='Context ID to retrieve')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def get(node, context_id, verbose):
    """Get information about a specific Calimero context."""
    manager = CalimeroManager()
    
    try:
        # Check if node is running
        try:
            container = manager.client.containers.get(node)
            if container.status != 'running':
                console.print(f"[red]Node {node} is not running[/red]")
                sys.exit(1)
        except Exception:
            console.print(f"[red]Node {node} not found[/red]")
            sys.exit(1)
        
        # Get admin API URL
        admin_url = get_node_rpc_url(node, manager)
        console.print(f"[blue]Getting context {context_id} from node {node} via {admin_url}[/blue]")
        
        # Run context retrieval
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(get_context_via_admin_api(admin_url, context_id))
            loop.close()
            
            if result['success']:
                context_data = result.get('data', {}).get('data', {}).get('context', {})
                
                if not context_data:
                    console.print(f"\n[yellow]Context {context_id} not found on node {node}[/yellow]")
                    return
                
                console.print(f"\n[green]Context Information:[/green]")
                
                # Create table
                table = Table(title=f"Context {context_id} Details", box=box.ROUNDED)
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")
                
                table.add_row("Context ID", context_data.get('context_id', 'Unknown'))
                table.add_row("Type", context_data.get('type', 'Unknown'))
                table.add_row("Network", context_data.get('network', 'Unknown'))
                table.add_row("Contract ID", context_data.get('contract_id', 'Unknown'))
                table.add_row("Signer Type", context_data.get('signer', {}).get('type', 'Unknown'))
                
                # Add signer-specific details
                signer = context_data.get('signer', {})
                if signer.get('url'):
                    table.add_row("Signer URL", signer['url'])
                if signer.get('account_id'):
                    table.add_row("Account ID", signer['account_id'])
                if signer.get('rpc_url'):
                    table.add_row("RPC URL", signer['rpc_url'])
                
                console.print(table)
                
                if verbose:
                    console.print(f"\n[bold]Full response:[/bold]")
                    console.print(f"{result}")
                    
            else:
                console.print(f"\n[red]✗ Failed to get context[/red]")
                console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
                sys.exit(1)
                
        except Exception as e:
            console.print(f"\n[red]✗ Failed to get context: {str(e)}[/red]")
            sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]Failed to get context: {str(e)}[/red]")
        sys.exit(1)

@context.command()
@click.option('--node', '-n', required=True, help='Node name to delete context from')
@click.option('--context-id', required=True, help='Context ID to delete')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def delete(node, context_id, verbose):
    """Delete a specific Calimero context."""
    manager = CalimeroManager()
    
    try:
        # Check if node is running
        try:
            container = manager.client.containers.get(node)
            if container.status != 'running':
                console.print(f"[red]Node {node} is not running[/red]")
                sys.exit(1)
        except Exception:
            console.print(f"[red]Node {node} not found[/red]")
            sys.exit(1)
        
        # Get admin API URL
        admin_url = get_node_rpc_url(node, manager)
        console.print(f"[blue]Deleting context {context_id} from node {node} via {admin_url}[/blue]")
        
        # Run context deletion
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(delete_context_via_api(admin_url, context_id))
            loop.close()
            
            if result['success']:
                console.print(f"\n[green]✓ Context {context_id} deleted successfully![/green]")
                
                if verbose:
                    console.print(f"\n[bold]Deletion response:[/bold]")
                    console.print(f"{result}")
                    
            else:
                console.print(f"\n[red]✗ Failed to delete context[/red]")
                console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
                sys.exit(1)
                
        except Exception as e:
            console.print(f"\n[red]✗ Failed to delete context: {str(e)}[/red]")
            sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]Failed to delete context: {str(e)}[/red]")
        sys.exit(1)

async def delete_context_via_api(rpc_url: str, context_id: str) -> dict:
    """Delete a specific Calimero context using the admin API."""
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            # Use the admin API endpoint for deleting context
            endpoint = f"{rpc_url}/admin-api/contexts/{context_id}"
            
            headers = {'Content-Type': 'application/json'}
            
            async with session.delete(endpoint, headers=headers, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    return {'success': True, 'data': result}
                else:
                    error_text = await response.text()
                    return {'success': False, 'error': f"HTTP {response.status}: {error_text}"}
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

if __name__ == '__main__':
    context()
