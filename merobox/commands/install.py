"""
Install command - Install applications on Calimero nodes using admin API.
"""

import click
import os
import asyncio
import sys
from typing import Dict, Any, Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import (
    get_node_rpc_url,
    console
)

async def install_application_via_admin_api(
    rpc_url: str, 
    url: str = None, 
    path: str = None, 
    metadata: bytes = None,
    is_dev: bool = False,
    node_name: str = None
) -> dict:
    """Install an application using the admin API."""
    try:
        # Import the admin client
        from calimero import AdminClient
        
        # Create admin client
        admin_client = AdminClient(rpc_url)
        
        if is_dev and path:
            # For dev installation, use the dedicated install-dev-application endpoint
            console.print(f"[blue]Installing development application from path: {path}[/blue]")
            
            # Copy the file to the container's data directory so the server can access it
            import shutil
            container_data_dir = f"data/{node_name.split('-')[0]}-{node_name.split('-')[1]}-{node_name.split('-')[2]}"
            if not os.path.exists(container_data_dir):
                # Try alternative naming pattern
                container_data_dir = f"data/{node_name}"
            
            if not os.path.exists(container_data_dir):
                return {'success': False, 'error': f"Container data directory not found: {container_data_dir}"}
            
            # Copy file to container data directory
            filename = os.path.basename(path)
            container_file_path = os.path.join(container_data_dir, filename)
            shutil.copy2(path, container_file_path)
            console.print(f"[blue]Copied file to container data directory: {container_file_path}[/blue]")
            
            # Use the container path that the server can access
            container_path = f"/app/data/{filename}"
            
            # Install using admin client
            result = await admin_client.install_dev_application(container_path, metadata or b"")
            
            if result['success']:
                result['path'] = path
                result['container_path'] = container_path
            
            return result
        else:
            # Install application from URL
            result = await admin_client.install_application(url, None, metadata or b"")
            
            if result['success']:
                result['url'] = url
            
            return result
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

def validate_installation_source(url: str = None, path: str = None, is_dev: bool = False) -> tuple[bool, str]:
    """Validate that either URL or path is provided based on installation type."""
    if is_dev:
        if not path:
            return False, "Development installation requires --path parameter"
        if not os.path.exists(path):
            return False, f"File not found: {path}"
        if not os.path.isfile(path):
            return False, f"Path is not a file: {path}"
        return True, ""
    else:
        if not url:
            return False, "Remote installation requires --url parameter"
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False, f"Invalid URL format: {url}"
            return True, ""
        except Exception:
            return False, f"Invalid URL: {url}"

@click.command()
@click.option('--node', '-n', required=True, help='Node name to install the application on')
@click.option('--url', help='URL to install the application from')
@click.option('--path', help='Local path for dev installation')
@click.option('--dev', is_flag=True, help='Install as development application from local path')
@click.option('--metadata', help='Application metadata (optional)')
@click.option('--timeout', default=30, help='Timeout in seconds for installation (default: 30)')
@click.option('--verbose', '-v', is_flag=True, help='Show verbose output')
def install(node, url, path, dev, metadata, timeout, verbose):
    """Install applications on Calimero nodes."""
    manager = CalimeroManager()
    
    # Check if node is running
    check_node_running(node, manager)
    
    # Validate installation source
    is_valid, error_msg = validate_installation_source(url, path, dev)
    if not is_valid:
        console.print(f"[red]✗ {error_msg}[/red]")
        sys.exit(1)
    
    # Parse metadata if provided
    metadata_bytes = b""
    if metadata:
        try:
            metadata_bytes = metadata.encode('utf-8')
        except Exception as e:
            console.print(f"[red]✗ Failed to encode metadata: {str(e)}[/red]")
            sys.exit(1)
    
    # Get admin API URL
    admin_url = get_node_rpc_url(node, manager)
    
    if dev:
        console.print(f"[blue]Installing development application on node {node} via {admin_url}[/blue]")
    else:
        console.print(f"[blue]Installing application from {url} on node {node} via {admin_url}[/blue]")
    
    # Run installation
    result = run_async_function(install_application_via_admin_api, admin_url, url, path, metadata_bytes, dev, node)
    
    if result['success']:
        console.print(f"\n[green]✓ Application installed successfully![/green]")
        
        if dev and 'container_path' in result:
            console.print(f"[blue]Container path: {result['container_path']}[/blue]")
        
        if verbose:
            console.print(f"\n[bold]Installation response:[/bold]")
            console.print(f"{result}")
            
    else:
        console.print(f"\n[red]✗ Failed to install application[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)
