"""
Install application step executor.
"""

from typing import Dict, Any
from ...utils import get_node_rpc_url, console
from ...install import install_application_via_admin_api
from .base import BaseStep


class InstallApplicationStep(BaseStep):
    """Execute an install application step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        application_path = self.config.get('path')
        application_url = self.config.get('url')
        is_dev = self.config.get('dev', False)
        
        if not application_path and not application_url:
            console.print("[red]No application path or URL specified[/red]")
            return False
        
        # Get node RPC URL
        try:
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute installation
        if is_dev and application_path:
            result = await install_application_via_admin_api(
                rpc_url, 
                path=application_path,
                is_dev=True,
                node_name=node_name
            )
        else:
            result = await install_application_via_admin_api(rpc_url, url=application_url)
        
        # Log detailed API response
        console.print(f"[cyan]🔍 Install API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")
        if not result.get('success'):
            console.print(f"  Error: {result.get('error')}")
        
        if result['success']:
            # Store result for later use
            step_key = f"install_{node_name}"
            workflow_results[step_key] = result['data']
            
            # Debug: Show what we actually received
            console.print(f"[blue]📝 Install result data: {result['data']}[/blue]")
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                # Handle nested data structure
                actual_data = result['data'].get('data', result['data'])
                app_id = actual_data.get('id', actual_data.get('applicationId', actual_data.get('name')))
                if app_id:
                    dynamic_values[f'app_id_{node_name}'] = app_id
                    console.print(f"[blue]📝 Captured application ID for {node_name}: {app_id}[/blue]")
                else:
                    console.print(f"[yellow]⚠️  No application ID found in response. Available keys: {list(actual_data.keys())}[/yellow]")
            else:
                console.print(f"[yellow]⚠️  Install result is not a dict: {type(result['data'])}[/yellow]")
            
            return True
        else:
            console.print(f"[red]Installation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
