"""
Base step class for all workflow steps.
"""

from typing import Dict, Any
from ...utils import console


class BaseStep:
    """Base class for all workflow steps."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        """Execute the step. Must be implemented by subclasses."""
        raise NotImplementedError
    
    def _resolve_dynamic_value(self, value: str, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> str:
        """Resolve dynamic values using placeholders and captured results."""
        if not isinstance(value, str):
            return value
            
        # Replace placeholders with actual values
        if value.startswith('{{') and value.endswith('}}'):
            placeholder = value[2:-2].strip()
            
            # Handle different placeholder types
            if placeholder.startswith('install.'):
                # Format: {{install.node_name}}
                parts = placeholder.split('.', 1)
                if len(parts) == 2:
                    node_name = parts[1]
                    # First try to get from dynamic values (captured application ID)
                    dynamic_key = f"app_id_{node_name}"
                    if dynamic_key in dynamic_values:
                        app_id = dynamic_values[dynamic_key]
                        return app_id
                    
                    # Fallback to workflow results
                    install_key = f"install_{node_name}"
                    if install_key in workflow_results:
                        result = workflow_results[install_key]
                        # Try to extract application ID from the result
                        if isinstance(result, dict):
                            return result.get('id', result.get('applicationId', result.get('name', value)))
                        return str(result)
                    else:
                        console.print(f"[yellow]Warning: Install result for {node_name} not found, using placeholder[/yellow]")
                        return value
            
            elif placeholder.startswith('context.'):
                # Format: {{context.node_name}} or {{context.node_name.field}}
                parts = placeholder.split('.', 1)
                if len(parts) == 2:
                    node_part = parts[1]
                    # Check if there's a field specification (e.g., context.node_name.memberPublicKey)
                    if '.' in node_part:
                        node_name, field_name = node_part.split('.', 1)
                    else:
                        node_name = node_part
                        field_name = None
                    
                    if field_name:
                        # For field access (e.g., memberPublicKey), look in workflow_results
                        context_key = f"context_{node_name}"
                        if context_key in workflow_results:
                            result = workflow_results[context_key]
                            # Try to extract specific field from the result
                            if isinstance(result, dict):
                                # Handle nested data structure
                                actual_data = result.get('data', result)
                                return actual_data.get(field_name, value)
                        else:
                            console.print(f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]")
                            return value
                    else:
                        # For context ID access, look in dynamic_values first
                        context_id_key = f"context_id_{node_name}"
                        if context_id_key in dynamic_values:
                            return dynamic_values[context_id_key]
                        
                        # Fallback to workflow_results
                        context_key = f"context_{node_name}"
                        if context_key in workflow_results:
                            result = workflow_results[context_key]
                            # Try to extract context ID from the result
                            if isinstance(result, dict):
                                # Handle nested data structure
                                actual_data = result.get('data', result)
                                return actual_data.get('id', actual_data.get('contextId', actual_data.get('name', value)))
                            return str(result)
                        else:
                            console.print(f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]")
                            return value
            
            elif placeholder.startswith('identity.'):
                # Format: {{identity.node_name}}
                parts = placeholder.split('.', 1)
                if len(parts) == 2:
                    node_name = parts[1]
                    identity_key = f"identity_{node_name}"
                    if identity_key in workflow_results:
                        result = workflow_results[identity_key]
                        # Try to extract public key from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get('data', result)
                            return actual_data.get('publicKey', actual_data.get('id', actual_data.get('name', value)))
                        return str(result)
                    else:
                        console.print(f"[yellow]Warning: Identity result for {node_name} not found, using placeholder[/yellow]")
                        return value
            
            elif placeholder.startswith('invite.'):
                # Format: {{invite.node_name_identity.node_name}}
                parts = placeholder.split('.', 1)
                if len(parts) == 2:
                    invite_part = parts[1]
                    # Parse the format: node_name_identity.node_name
                    if '_identity.' in invite_part:
                        inviter_node, identity_node = invite_part.split('_identity.', 1)
                        # First resolve the identity to get the actual public key
                        identity_placeholder = f"{{{{identity.{identity_node}}}}}"
                        actual_identity = self._resolve_dynamic_value(identity_placeholder, workflow_results, dynamic_values)
                        
                        # Now construct the invite key using the actual identity
                        invite_key = f"invite_{inviter_node}_{actual_identity}"
                        
                        if invite_key in workflow_results:
                            result = workflow_results[invite_key]
                            # Try to extract invitation data from the result
                            if isinstance(result, dict):
                                # Handle nested data structure
                                actual_data = result.get('data', result)
                                return actual_data.get('invitation', actual_data.get('id', actual_data.get('name', value)))
                            return str(result)
                        else:
                            console.print(f"[yellow]Warning: Invite result for {invite_key} not found, using placeholder[/yellow]")
                            return value
                    else:
                        console.print(f"[yellow]Warning: Invalid invite placeholder format {placeholder}, using as-is[/yellow]")
                        return value
            
            elif placeholder in dynamic_values:
                return dynamic_values[placeholder]
            
            # Handle iteration placeholders
            elif placeholder.startswith('iteration'):
                # Format: {{iteration}}, {{iteration_index}}, etc.
                if placeholder in dynamic_values:
                    return str(dynamic_values[placeholder])
                else:
                    console.print(f"[yellow]Warning: Iteration placeholder {placeholder} not found, using as-is[/yellow]")
                    return value
            
            else:
                console.print(f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]")
                return value
        
        return value
