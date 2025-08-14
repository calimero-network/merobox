"""
Create context step executor.
"""

from typing import Dict, Any
from ...utils import get_node_rpc_url, console
from ...context import create_context_via_admin_api
from .base import BaseStep


class CreateContextStep(BaseStep):
    """Execute a create context step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        application_id = self._resolve_dynamic_value(self.config['application_id'], workflow_results, dynamic_values)
        
        # Get node RPC URL
        try:
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute context creation
        result = await create_context_via_admin_api(rpc_url, application_id)
        
        # Log detailed API response
        console.print(f"[cyan]🔍 Context Creation API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")
        if not result.get('success'):
            console.print(f"  Error: {result.get('error')}")
        
        if result['success']:
            # Store result for later use
            step_key = f"context_{node_name}"
            workflow_results[step_key] = result['data']
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                context_id = result['data'].get('id', result['data'].get('contextId', result['data'].get('name')))
                if context_id:
                    dynamic_values[f'context_id_{node_name}'] = context_id
                    console.print(f"[blue]📝 Captured context ID for {node_name}: {context_id}[/blue]")
            
            return True
        else:
            console.print(f"[red]Context creation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
