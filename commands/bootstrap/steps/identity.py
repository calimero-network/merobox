"""
Identity management step executors.
"""

from typing import Dict, Any
from ...utils import get_node_rpc_url, console
from ...identity import generate_identity_via_admin_api, invite_identity_via_admin_api
from .base import BaseStep


class CreateIdentityStep(BaseStep):
    """Execute a create identity step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        
        # Get node RPC URL
        try:
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute identity creation
        result = await generate_identity_via_admin_api(rpc_url)
        
        # Log detailed API response
        console.print(f"[cyan]🔍 Identity Creation API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")
        if not result.get('success'):
            console.print(f"  Error: {result.get('error')}")
        
        if result['success']:
            # Store result for later use
            step_key = f"identity_{node_name}"
            workflow_results[step_key] = result['data']
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                public_key = result['data'].get('publicKey', result['data'].get('id', result['data'].get('name')))
                if public_key:
                    dynamic_values[f'public_key_{node_name}'] = public_key
                    console.print(f"[blue]📝 Captured public key for {node_name}: {public_key}[/blue]")
            
            return True
        else:
            console.print(f"[red]Identity creation failed: {result.get('error', 'Unknown error')}[/red]")
            return False


class InviteIdentityStep(BaseStep):
    """Execute an invite identity step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        context_id = self._resolve_dynamic_value(self.config['context_id'], workflow_results, dynamic_values)
        inviter_id = self._resolve_dynamic_value(self.config['granter_id'], workflow_results, dynamic_values)
        invitee_id = self._resolve_dynamic_value(self.config['grantee_id'], workflow_results, dynamic_values)
        capability = self.config.get('capability', 'member')
        
        # Get node RPC URL
        try:
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute invitation
        result = await invite_identity_via_admin_api(
            rpc_url, context_id, inviter_id, invitee_id, capability
        )
        
        # Log detailed API response
        console.print(f"[cyan]🔍 Invitation API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")
        console.print(f"  Data: {result.get('data')}")
        console.print(f"  Endpoint: {result.get('endpoint', 'N/A')}")
        console.print(f"  Payload Format: {result.get('payload_format', 'N/A')}")
        if not result.get('success'):
            console.print(f"  Error: {result.get('error')}")
            if 'tried_payloads' in result:
                console.print(f"  Tried Payloads: {result['tried_payloads']}")
            if 'errors' in result:
                console.print(f"  Detailed Errors: {result['errors']}")
        
        if result['success']:
            # Store result for later use
            step_key = f"invite_{node_name}_{invitee_id}"
            # Extract the actual invitation data from the nested response
            invitation_data = result['data'].get('data') if isinstance(result['data'], dict) else result['data']
            workflow_results[step_key] = invitation_data
            return True
        else:
            console.print(f"[red]Invitation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
