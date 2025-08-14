"""
Individual step executors for bootstrap workflow steps.
"""

import asyncio
from typing import Dict, Any, Optional
from ..utils import get_node_rpc_url, console
from ..install import install_application_via_admin_api
from ..context import create_context_via_admin_api
from ..identity import generate_identity_via_admin_api, invite_identity_via_admin_api
from ..join import join_context_via_admin_api
from ..call import call_function

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
            from ..manager import CalimeroManager
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

class CreateContextStep(BaseStep):
    """Execute a create context step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        application_id = self._resolve_dynamic_value(self.config['application_id'], workflow_results, dynamic_values)
        
        # Get node RPC URL
        try:
            from ..manager import CalimeroManager
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

class CreateIdentityStep(BaseStep):
    """Execute a create identity step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        
        # Get node RPC URL
        try:
            from ..manager import CalimeroManager
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
            from ..manager import CalimeroManager
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

class JoinContextStep(BaseStep):
    """Execute a join context step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        node_name = self.config['node']
        context_id = self._resolve_dynamic_value(self.config['context_id'], workflow_results, dynamic_values)
        invitee_id = self._resolve_dynamic_value(self.config['invitee_id'], workflow_results, dynamic_values)
        invitation = self._resolve_dynamic_value(self.config['invitation'], workflow_results, dynamic_values)
        
        # Debug: Show resolved values
        console.print(f"[blue]Debug: Resolved values for join step:[/blue]")
        console.print(f"  context_id: {context_id}")
        console.print(f"  invitee_id: {invitee_id}")
        console.print(f"  invitation: {invitation[:50] if isinstance(invitation, str) and len(invitation) > 50 else invitation}")
        console.print(f"  invitation type: {type(invitation)}")
        console.print(f"  invitation length: {len(invitation) if isinstance(invitation, str) else 'N/A'}")
        
        # Get node RPC URL
        try:
            from ..manager import CalimeroManager
            manager = CalimeroManager()
            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute join
        console.print(f"[blue]About to call join function...[/blue]")
        result = await join_context_via_admin_api(
            rpc_url, context_id, invitee_id, invitation
        )
        console.print(f"[blue]Join function returned: {result}[/blue]")
        
        # Log detailed API response
        console.print(f"[cyan]🔍 Join API Response for {node_name}:[/cyan]")
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
            step_key = f"join_{node_name}_{invitee_id}"
            workflow_results[step_key] = result['data']
            return True
        else:
            console.print(f"[red]Join failed: {result.get('error', 'Unknown error')}[/red]")
            return False

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
            from ..manager import CalimeroManager
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

class WaitStep(BaseStep):
    """Execute a wait step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        wait_seconds = self.config.get('seconds', 5)
        console.print(f"Waiting {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)
        return True

class RepeatStep(BaseStep):
    """Execute nested steps multiple times."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        repeat_count = self.config.get('count', 1)
        nested_steps = self.config.get('steps', [])
        step_name = self.config.get('name', 'Repeat Step')
        
        if not nested_steps:
            console.print("[yellow]No nested steps specified for repeat[/yellow]")
            return True
        
        console.print(f"[cyan]🔄 Executing {len(nested_steps)} nested steps {repeat_count} times...[/cyan]")
        
        for iteration in range(repeat_count):
            console.print(f"\n[bold blue]📋 Iteration {iteration + 1}/{repeat_count}[/bold blue]")
            
            # Create iteration-specific dynamic values
            iteration_dynamic_values = dynamic_values.copy()
            iteration_dynamic_values.update({
                'iteration': iteration + 1,
                'iteration_index': iteration,
                'iteration_zero_based': iteration,
                'iteration_one_based': iteration + 1
            })
            
            # Execute each nested step in sequence
            for step_idx, step in enumerate(nested_steps):
                step_type = step.get('type')
                nested_step_name = step.get('name', f"Nested Step {step_idx + 1}")
                
                console.print(f"  [cyan]Executing {nested_step_name} ({step_type})...[/cyan]")
                
                try:
                    # Create appropriate step executor for the nested step
                    step_executor = self._create_nested_step_executor(step_type, step)
                    if not step_executor:
                        console.print(f"[red]Unknown nested step type: {step_type}[/red]")
                        return False
                    
                    # Execute the nested step with iteration-specific dynamic values
                    success = await step_executor.execute(workflow_results, iteration_dynamic_values)
                    
                    if not success:
                        console.print(f"[red]❌ Nested step '{nested_step_name}' failed in iteration {iteration + 1}[/red]")
                        return False
                    
                    console.print(f"  [green]✓ Nested step '{nested_step_name}' completed in iteration {iteration + 1}[/green]")
                    
                except Exception as e:
                    console.print(f"[red]❌ Nested step '{nested_step_name}' failed with error in iteration {iteration + 1}: {str(e)}[/red]")
                    return False
        
        console.print(f"[green]✓ All {repeat_count} iterations completed successfully[/green]")
        return True
    
    def _create_nested_step_executor(self, step_type: str, step_config: Dict[str, Any]):
        """Create the appropriate step executor for nested steps."""
        if step_type == 'install_application':
            return InstallApplicationStep(step_config)
        elif step_type == 'create_context':
            return CreateContextStep(step_config)
        elif step_type == 'create_identity':
            return CreateIdentityStep(step_config)
        elif step_type == 'invite_identity':
            return InviteIdentityStep(step_config)
        elif step_type == 'join_context':
            return JoinContextStep(step_config)
        elif step_type == 'call':
            return ExecuteStep(step_config)
        elif step_type == 'wait':
            return WaitStep(step_config)
        else:
            return None
