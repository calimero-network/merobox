"""
Install application step executor.
"""

from typing import Dict, Any, List
from ...utils import get_node_rpc_url, console
from ...install import install_application_via_admin_api
from .base import BaseStep


class InstallApplicationStep(BaseStep):
    """Execute an install application step."""
    
    def _get_required_fields(self) -> List[str]:
        """
        Define which fields are required for this step.
        
        Returns:
            List of required field names
        """
        return ['node']
    
    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get('name', f'Unnamed {self.config.get("type", "Unknown")} step')
        
        # Validate node is a string
        if not isinstance(self.config.get('node'), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        
        # Validate path is a string if provided
        if 'path' in self.config and not isinstance(self.config['path'], str):
            raise ValueError(f"Step '{step_name}': 'path' must be a string")
        
        # Validate url is a string if provided
        if 'url' in self.config and not isinstance(self.config['url'], str):
            raise ValueError(f"Step '{step_name}': 'url' must be a string")
        
        # Validate dev is a boolean if provided
        if 'dev' in self.config and not isinstance(self.config['dev'], bool):
            raise ValueError(f"Step '{step_name}': 'dev' must be a boolean")
        
        # Validate that either path or url is provided
        if 'path' not in self.config and 'url' not in self.config:
            raise ValueError(f"Step '{step_name}': Either 'path' or 'url' must be provided")
    
    def _get_exportable_variables(self):
        """
        Define which variables this step can export.
        
        Available variables from install_application API response:
        - applicationId: Application ID (this is what the API actually returns)
        """
        return [
            ('applicationId', 'app_id_{node_name}', 'Application ID - primary identifier for the installed application'),
        ]
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        application_path = self.config.get('path')
        application_url = self.config.get('url')
        is_dev = self.config.get('dev', False)
        
        # Validate export configuration
        if not self._validate_export_config():
            console.print(f"[yellow]⚠️  Install step export configuration validation failed[/yellow]")
        
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
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result['data']):
                return False
            
            # Store result for later use
            step_key = f"install_{node_name}"
            workflow_results[step_key] = result['data']
            
            # Debug: Show what we actually received
            console.print(f"[blue]📝 Install result data: {result['data']}[/blue]")
            
            # Export variables using the new standardized approach
            self._export_variables(result['data'], node_name, dynamic_values)
            
            # Legacy support: ensure app_id is always available for backward compatibility
            if f'app_id_{node_name}' not in dynamic_values:
                # Try to extract from the raw response as fallback
                if isinstance(result['data'], dict):
                    actual_data = result['data'].get('data', result['data'])
                    app_id = actual_data.get('id', actual_data.get('applicationId', actual_data.get('name')))
                    if app_id:
                        dynamic_values[f'app_id_{node_name}'] = app_id
                        console.print(f"[blue]📝 Fallback: Captured application ID for {node_name}: {app_id}[/blue]")
                    else:
                        console.print(f"[yellow]⚠️  No application ID found in response. Available keys: {list(actual_data.keys())}[/yellow]")
                else:
                    console.print(f"[yellow]⚠️  Install result is not a dict: {type(result['data'])}[/yellow]")
            
            return True
        else:
            console.print(f"[red]Installation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
