"""
Execute step executor for contract calls.
"""

from typing import Dict, Any
from ...utils import get_node_rpc_url, console
from ...call import call_function
from .base import BaseStep


class ExecuteStep(BaseStep):
    """Execute a contract execution step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        context_id = self._resolve_dynamic_value(self.config['context_id'], workflow_results, dynamic_values)
        exec_type = self.config.get('exec_type')  # Get exec_type if specified, otherwise will default to function_call
        method = self.config.get('method')
        args = self.config.get('args', {})

        # Debug: Show resolved values
        console.print(f"[blue]Debug: Resolved values for execute step:[/blue]")
        console.print(f"  context_id: {context_id}")
        console.print(f"  exec_type: {exec_type}")
        console.print(f"  method: {method}")
        console.print(f"  args: {args}")
        
        # Get executor public key from the context that was created
        executor_public_key = None
        # Extract node name from the original context_id placeholder (e.g., {{context.calimero-node-1}})
        original_context_id = self.config['context_id']
        if '{{context.' in original_context_id and '}}' in original_context_id:
            context_node = original_context_id.split('{{context.')[1].split('}}')[0]
            context_key = f"context_{context_node}"
            console.print(f"[blue]Debug: Looking for context key: {context_key}[/blue]")
            if context_key in workflow_results:
                context_data = workflow_results[context_key]
                console.print(f"[blue]Debug: Context data: {context_data}[/blue]")
                if isinstance(context_data, dict) and 'data' in context_data:
                    executor_public_key = context_data['data'].get('memberPublicKey')
                    console.print(f"[blue]Debug: Found executor public key: {executor_public_key}[/blue]")
                else:
                    console.print(f"[blue]Debug: Context data structure: {type(context_data)}[/blue]")
            else:
                console.print(f"[blue]Debug: Context key {context_key} not found in workflow_results[/blue]")
                console.print(f"[blue]Debug: Available keys: {list(workflow_results.keys())}[/blue]")
        
        # Get node RPC URL
        try:
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute based on type
        try:
            # Default to function_call if exec_type is not specified
            if not exec_type:
                exec_type = 'function_call'
            
            if exec_type in ['contract_call', 'view_call', 'function_call']:
                result = await call_function(
                    rpc_url, context_id, method, args, executor_public_key
                )
            else:
                console.print(f"[red]Unknown execution type: {exec_type}[/red]")
                return False
            
            # Log detailed API response
            console.print(f"[cyan]🔍 Execute API Response for {node_name}:[/cyan]")
            console.print(f"  Success: {result.get('success')}")
            console.print(f"  Data: {result.get('data')}")
            if not result.get('success'):
                console.print(f"  Error: {result.get('error')}")
            
            if result['success']:
                # Store result for later use
                step_key = f"execute_{node_name}_{method}"
                workflow_results[step_key] = result['data']
                return True
            else:
                console.print(f"[red]Execution failed: {result.get('error', 'Unknown error')}[/red]")
                return False
                
        except Exception as e:
            console.print(f"[red]Execution failed with error: {str(e)}[/red]")
            return False
