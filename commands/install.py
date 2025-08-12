"""
Install command - Install applications on Calimero nodes using JSON-RPC admin API.
"""

import click
import asyncio
import sys
import os
import hashlib
import json
import base64
from pathlib import Path
from urllib.parse import urlparse
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn
from .manager import CalimeroManager
from .utils import (
    get_node_rpc_url, 
    check_node_running, 
    run_async_function,
    console
)

async def install_application_via_api(
    rpc_url: str, 
    url: str = None, 
    path: str = None, 
    metadata: bytes = None,
    is_dev: bool = False,
    node_name: str = None
) -> dict:
    """Install an application using the admin API."""
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
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
                
                endpoint = f"{rpc_url}/admin-api/install-dev-application"
                
                # Use the container path that the server can access
                container_path = f"/app/data/{filename}"
                
                # Create JSON payload with container path and metadata
                payload = {
                    "path": container_path,
                    "metadata": list(metadata) if metadata else []
                }
                
                headers = {'Content-Type': 'application/json'}
                
                async with session.post(endpoint, json=payload, headers=headers, timeout=60) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {'success': True, 'data': result, 'path': path, 'container_path': container_path}
                    else:
                        error_text = await response.text()
                        return {'success': False, 'error': f"HTTP {response.status}: {error_text}"}
            else:
                # Install application from URL
                endpoint = f"{rpc_url}/admin-api/install-application"
                
                # Calculate hash if URL is provided
                hash_value = None
                if url:
                    try:
                        # For now, we'll use a placeholder hash
                        # In a real implementation, you might want to download and hash the file
                        hash_value = hashlib.sha256(url.encode()).hexdigest()
                    except Exception:
                        hash_value = None
                
                # Create JSON payload
                payload = {
                    "url": url,
                    "metadata": list(metadata) if metadata else []
                }
                
                # Only include hash if it's provided and valid
                # For now, let's skip the hash to avoid format issues
                # if hash_value:
                #     payload["hash"] = hash_value
                
                headers = {'Content-Type': 'application/json'}
                
                async with session.post(endpoint, json=payload, headers=headers, timeout=60) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {'success': True, 'data': result, 'url': url}
                    else:
                        error_text = await response.text()
                        return {'success': False, 'error': f"HTTP {response.status}: {error_text}"}
        
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
    result = run_async_function(install_application_via_api, admin_url, url, path, metadata_bytes, dev, node)
    
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
