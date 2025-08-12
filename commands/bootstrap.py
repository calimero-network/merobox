"""
Bootstrap command - Automate Calimero node workflows using YAML configuration files.
"""

import click
import asyncio
import sys
import os
import yaml
import time
import docker
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.text import Text
from typing import Dict, List, Any, Optional
from .manager import CalimeroManager
from .utils import (
    get_node_rpc_url, 
    check_node_running, 
    run_async_function,
    console
)

# Import the async functions from other commands
from .install import install_application_via_admin_api
from .context import create_context_via_admin_api
from .identity import generate_identity_via_admin_api, invite_identity_via_admin_api

class WorkflowExecutor:
    """Executes Calimero workflows based on YAML configuration."""
    
    def __init__(self, config: Dict[str, Any], manager: CalimeroManager):
        self.config = config
        self.manager = manager
        self.workflow_results = {}
        self.dynamic_values = {}  # Store dynamic values for later use
        
    async def execute_workflow(self) -> bool:
        """Execute the complete workflow."""
        workflow_name = self.config.get('name', 'Unnamed Workflow')
        console.print(f"\n[bold blue]🚀 Executing Workflow: {workflow_name}[/bold blue]")
        
        try:
            # Step 1: Stop all nodes if requested
            if self.config.get('stop_all_nodes', False):
                console.print("\n[bold yellow]Step 1: Stopping all nodes...[/bold yellow]")
                if not self.manager.stop_all_nodes():
                    console.print("[red]Failed to stop all nodes[/red]")
                    return False
                console.print("[green]✓ All nodes stopped[/green]")
                time.sleep(2)  # Give time for cleanup
            
            # Step 2: Start nodes
            console.print("\n[bold yellow]Step 2: Starting nodes...[/bold yellow]")
            if not await self._start_nodes():
                return False
            
            # Step 3: Wait for nodes to be ready
            console.print("\n[bold yellow]Step 3: Waiting for nodes to be ready...[/bold yellow]")
            if not await self._wait_for_nodes_ready():
                return False
            
            # Step 4: Execute workflow steps
            console.print("\n[bold yellow]Step 4: Executing workflow steps...[/bold yellow]")
            if not await self._execute_workflow_steps():
                return False
            
            console.print(f"\n[bold green]🎉 Workflow '{workflow_name}' completed successfully![/bold green]")
            
            # Display captured dynamic values
            if self.dynamic_values:
                console.print("\n[bold]📋 Captured Dynamic Values:[/bold]")
                for key, value in self.dynamic_values.items():
                    console.print(f"  {key}: {value}")
            
            return True
            
        except Exception as e:
            console.print(f"\n[red]❌ Workflow failed with error: {str(e)}[/red]")
            return False
    
    def _resolve_dynamic_value(self, value: str) -> str:
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
                    if dynamic_key in self.dynamic_values:
                        app_id = self.dynamic_values[dynamic_key]
                        return app_id
                    
                    # Fallback to workflow results
                    install_key = f"install_{node_name}"
                    if install_key in self.workflow_results:
                        result = self.workflow_results[install_key]
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
                    
                    context_key = f"context_{node_name}"
                    if context_key in self.workflow_results:
                        result = self.workflow_results[context_key]
                        # Try to extract context ID or specific field from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get('data', result)
                            if field_name:
                                # Return specific field (e.g., memberPublicKey)
                                return actual_data.get(field_name, value)
                            else:
                                # Return context ID
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
                    if identity_key in self.workflow_results:
                        result = self.workflow_results[identity_key]
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
                        actual_identity = self._resolve_dynamic_value(identity_placeholder)
                        
                        # Now construct the invite key using the actual identity
                        invite_key = f"invite_{inviter_node}_{actual_identity}"
                        
                        if invite_key in self.workflow_results:
                            result = self.workflow_results[invite_key]
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
            
            elif placeholder in self.dynamic_values:
                return self.dynamic_values[placeholder]
            
            else:
                console.print(f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]")
                return value
        
        return value
    
    async def _start_nodes(self) -> bool:
        """Start the configured nodes."""
        nodes_config = self.config.get('nodes', {})
        
        if not nodes_config:
            console.print("[red]No nodes configuration found[/red]")
            return False
        
        # Handle multiple nodes
        if 'count' in nodes_config:
            count = nodes_config['count']
            prefix = nodes_config.get('prefix', 'calimero-node')
            chain_id = nodes_config.get('chain_id', 'testnet-1')
            image = nodes_config.get('image')
            
            console.print(f"Starting {count} nodes with prefix '{prefix}'...")
            if not self.manager.run_multiple_nodes(count, prefix=prefix, chain_id=chain_id, image=image):
                return False
        else:
            # Handle individual node configurations
            for node_name, node_config in nodes_config.items():
                if isinstance(node_config, dict):
                    # Check if node already exists and is running
                    try:
                        existing_container = self.manager.client.containers.get(node_name)
                        if existing_container.status == 'running':
                            console.print(f"[green]✓ Node '{node_name}' is already running[/green]")
                            continue
                        else:
                            console.print(f"[yellow]Node '{node_name}' exists but not running, attempting to start...[/yellow]")
                    except docker.errors.NotFound:
                        # Node doesn't exist, create it
                        port = node_config.get('port', 2428)
                        rpc_port = node_config.get('rpc_port', 2528)
                        chain_id = node_config.get('chain_id', 'testnet-1')
                        image = node_config.get('image')
                        data_dir = node_config.get('data_dir')
                        
                        console.print(f"Starting node '{node_name}'...")
                        if not self.manager.run_node(node_name, port, rpc_port, chain_id, data_dir, image):
                            return False
                else:
                    # Simple string configuration (just node name)
                    # Check if node already exists and is running
                    try:
                        existing_container = self.manager.client.containers.get(node_config)
                        if existing_container.status == 'running':
                            console.print(f"[green]✓ Node '{node_config}' is already running[/green]")
                            continue
                        else:
                            console.print(f"[yellow]Node '{node_config}' exists but not running, attempting to start...[/yellow]")
                    except docker.errors.NotFound:
                        # Node doesn't exist, create it
                        console.print(f"Starting node '{node_config}'...")
                        if not self.manager.run_node(node_config):
                            return False
        
        console.print("[green]✓ All nodes are ready[/green]")
        return True
    
    async def _wait_for_nodes_ready(self) -> bool:
        """Wait for all nodes to be ready and accessible."""
        nodes_config = self.config.get('nodes', {})
        wait_timeout = self.config.get('wait_timeout', 60)  # Default 60 seconds
        
        if 'count' in nodes_config:
            count = nodes_config['count']
            prefix = nodes_config.get('prefix', 'calimero-node')
            node_names = [f"{prefix}-{i+1}" for i in range(count)]
        else:
            node_names = list(nodes_config.keys()) if isinstance(nodes_config, dict) else list(nodes_config)
        
        console.print(f"Waiting up to {wait_timeout} seconds for {len(node_names)} nodes to be ready...")
        
        start_time = time.time()
        ready_nodes = set()
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Waiting for nodes...", total=len(node_names))
            
            while len(ready_nodes) < len(node_names) and (time.time() - start_time) < wait_timeout:
                for node_name in node_names:
                    if node_name not in ready_nodes:
                        try:
                            # Check if node is running
                            container = self.manager.client.containers.get(node_name)
                            if container.status == 'running':
                                # Try to verify admin binding
                                if self.manager.verify_admin_binding(node_name):
                                    ready_nodes.add(node_name)
                                    progress.update(task, completed=len(ready_nodes))
                                    console.print(f"[green]✓ Node {node_name} is ready[/green]")
                        except Exception:
                            pass
                
                if len(ready_nodes) < len(node_names):
                    await asyncio.sleep(2)
        
        if len(ready_nodes) == len(node_names):
            console.print("[green]✓ All nodes are ready[/green]")
            return True
        else:
            missing_nodes = set(node_names) - ready_nodes
            console.print(f"[red]❌ Nodes not ready: {', '.join(missing_nodes)}[/red]")
            return False
    
    async def _execute_workflow_steps(self) -> bool:
        """Execute the configured workflow steps."""
        steps = self.config.get('steps', [])
        
        if not steps:
            console.print("[yellow]No workflow steps configured[/yellow]")
            return True
        
        for i, step in enumerate(steps, 1):
            step_type = step.get('type')
            step_name = step.get('name', f"Step {i}")
            
            console.print(f"\n[bold cyan]Executing {step_name} ({step_type})...[/bold cyan]")
            
            try:
                if step_type == 'install_application':
                    success = await self._execute_install_step(step)
                elif step_type == 'create_context':
                    success = await self._execute_context_step(step)
                elif step_type == 'create_identity':
                    success = await self._execute_identity_step(step)
                elif step_type == 'invite_identity':
                    success = await self._execute_invite_step(step)
                elif step_type == 'wait':
                    success = await self._execute_wait_step(step)
                elif step_type == 'join_context':
                    success = await self._execute_join_step(step)
                else:
                    console.print(f"[red]Unknown step type: {step_type}[/red]")
                    return False
                
                if not success:
                    console.print(f"[red]❌ Step '{step_name}' failed[/red]")
                    return False
                
                console.print(f"[green]✓ Step '{step_name}' completed[/green]")
                
            except Exception as e:
                console.print(f"[red]❌ Step '{step_name}' failed with error: {str(e)}[/red]")
                return False
        
        return True
    
    async def _execute_install_step(self, step: Dict[str, Any]) -> bool:
        """Execute an install application step."""
        node_name = step['node']
        application_path = step.get('path')
        application_url = step.get('url')
        is_dev = step.get('dev', False)
        
        if not application_path and not application_url:
            console.print("[red]No application path or URL specified[/red]")
            return False
        
        # Get node RPC URL
        try:
            container = self.manager.client.containers.get(node_name)
            rpc_url = get_node_rpc_url(node_name, self.manager)
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
            self.workflow_results[step_key] = result['data']
            
            # Debug: Show what we actually received
            console.print(f"[blue]📝 Install result data: {result['data']}[/blue]")
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                # Handle nested data structure
                actual_data = result['data'].get('data', result['data'])
                app_id = actual_data.get('id', actual_data.get('applicationId', actual_data.get('name')))
                if app_id:
                    self.dynamic_values[f'app_id_{node_name}'] = app_id
                    console.print(f"[blue]📝 Captured application ID for {node_name}: {app_id}[/blue]")
                else:
                    console.print(f"[yellow]⚠️  No application ID found in response. Available keys: {list(actual_data.keys())}[/yellow]")
            else:
                console.print(f"[yellow]⚠️  Install result is not a dict: {type(result['data'])}[/yellow]")
            
            return True
        else:
            console.print(f"[red]Installation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
    
    async def _execute_context_step(self, step: Dict[str, Any]) -> bool:
        """Execute a create context step."""
        node_name = step['node']
        application_id = self._resolve_dynamic_value(step['application_id'])
        
        # Get node RPC URL
        try:
            rpc_url = get_node_rpc_url(node_name, self.manager)
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
            self.workflow_results[step_key] = result['data']
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                context_id = result['data'].get('id', result['data'].get('contextId', result['data'].get('name')))
                if context_id:
                    self.dynamic_values[f'context_id_{node_name}'] = context_id
                    console.print(f"[blue]📝 Captured context ID for {node_name}: {context_id}[/blue]")
            
            return True
        else:
            console.print(f"[red]Context creation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
    
    async def _execute_identity_step(self, step: Dict[str, Any]) -> bool:
        """Execute a create identity step."""
        node_name = step['node']
        
        # Get node RPC URL
        try:
            rpc_url = get_node_rpc_url(node_name, self.manager)
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
            self.workflow_results[step_key] = result['data']
            
            # Extract and store key information
            if isinstance(result['data'], dict):
                public_key = result['data'].get('publicKey', result['data'].get('id', result['data'].get('name')))
                if public_key:
                    self.dynamic_values[f'public_key_{node_name}'] = public_key
                    console.print(f"[blue]📝 Captured public key for {node_name}: {public_key}[/blue]")
            
            return True
        else:
            console.print(f"[red]Identity creation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
    
    async def _execute_invite_step(self, step: Dict[str, Any]) -> bool:
        """Execute an invite identity step."""
        node_name = step['node']
        context_id = self._resolve_dynamic_value(step['context_id'])
        inviter_id = self._resolve_dynamic_value(step['granter_id'])  # Keep granter_id for backward compatibility
        invitee_id = self._resolve_dynamic_value(step['grantee_id'])  # Keep grantee_id for backward compatibility
        capability = step.get('capability', 'member')
        
        # Get node RPC URL
        try:
            rpc_url = get_node_rpc_url(node_name, self.manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute invitation
        from .identity import invite_identity_via_admin_api
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
            self.workflow_results[step_key] = invitation_data
            return True
        else:
            console.print(f"[red]Invitation failed: {result.get('error', 'Unknown error')}[/red]")
            return False
    
    async def _execute_wait_step(self, step: Dict[str, Any]) -> bool:
        """Execute a wait step."""
        wait_seconds = step.get('seconds', 5)
        console.print(f"Waiting {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)
        return True

    async def _execute_join_step(self, step: Dict[str, Any]) -> bool:
        """Execute a join context step."""
        node_name = step['node']
        context_id = self._resolve_dynamic_value(step['context_id'])
        invitee_id = self._resolve_dynamic_value(step['invitee_id'])
        invitation = self._resolve_dynamic_value(step['invitation'])
        
        # Debug: Show resolved values
        console.print(f"[blue]Debug: Resolved values for join step:[/blue]")
        console.print(f"  context_id: {context_id}")
        console.print(f"  invitee_id: {invitee_id}")
        console.print(f"  invitation: {invitation[:50] if isinstance(invitation, str) and len(invitation) > 50 else invitation}")
        console.print(f"  invitation type: {type(invitation)}")
        console.print(f"  invitation length: {len(invitation) if isinstance(invitation, str) else 'N/A'}")
        
        # Get node RPC URL
        try:
            rpc_url = get_node_rpc_url(node_name, self.manager)
        except Exception as e:
            console.print(f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]")
            return False
        
        # Execute join
        console.print(f"[blue]About to import join function...[/blue]")
        from .join import join_context_via_admin_api
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
            self.workflow_results[step_key] = result['data']
            return True
        else:
            console.print(f"[red]Join failed: {result.get('error', 'Unknown error')}[/red]")
            return False

def load_workflow_config(config_path: str) -> Dict[str, Any]:
    """Load workflow configuration from YAML file."""
    try:
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
        
        # Validate required fields
        required_fields = ['name', 'nodes']
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Missing required field: {field}")
        
        return config
        
    except FileNotFoundError:
        raise FileNotFoundError(f"Workflow configuration file not found: {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML format: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to load configuration: {str(e)}")

def create_sample_workflow_config(output_path: str = "workflow-example.yml"):
    """Create a sample workflow configuration file."""
    sample_config = {
        'name': 'Sample Calimero Workflow',
        'description': 'A sample workflow that demonstrates the bootstrap functionality',
        'stop_all_nodes': True,  # Stop all existing nodes before starting
        'wait_timeout': 60,  # Wait up to 60 seconds for nodes to be ready
        
        'nodes': {
            'count': 2,
            'prefix': 'calimero-node',
            'chain_id': 'testnet-1',
            'image': 'ghcr.io/calimero-network/merod:6a47604'
        },
        
        'steps': [
            {
                'name': 'Install Application on Node 1',
                'type': 'install_application',
                'node': 'calimero-node-1',
                'path': './kv_store.wasm',
                'dev': True
            },
            {
                'name': 'Create Context on Node 1',
                'type': 'create_context',
                'node': 'calimero-node-1',
                'application_id': '{{install.calimero-node-1}}'
            },
            {
                'name': 'Create Identity on Node 2',
                'type': 'create_identity',
                'node': 'calimero-node-2'
            },
            {
                'name': 'Wait for Identity Creation',
                'type': 'wait',
                'seconds': 5
            },
            {
                'name': 'Invite Node 2 from Node 1',
                'type': 'invite_identity',
                'context_id': '{{context.calimero-node-1}}',
                'invitee_id': '{{identity.calimero-node-2}}',
                'granter_id': '{{context.calimero-node-1.memberPublicKey}}',
                'capability': 'member',
                'node': 'calimero-node-1'
            },
            {
                'name': 'Join Context from Node 2',
                'type': 'join_context',
                'context_id': '{{context.calimero-node-1}}',
                'invitee_id': '{{identity.calimero-node-2}}',
                'invitation': '{{invite.calimero-node-1_identity.calimero-node-2}}',
                'node': 'calimero-node-2'
            }
        ]
    }
    
    try:
        with open(output_path, 'w') as file:
            yaml.dump(sample_config, file, default_flow_style=False, indent=2)
        
        console.print(f"[green]✓ Sample workflow configuration created: {output_path}[/green]")
        console.print("[yellow]Note: Dynamic values are automatically captured and used with placeholders[/yellow]")
        
    except Exception as e:
        console.print(f"[red]Failed to create sample configuration: {str(e)}[/red]")

@click.command()
@click.argument('config_file', type=click.Path(exists=True))
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
def bootstrap(config_file, verbose):
    """Run a workflow from a YAML configuration file."""
    try:
        # Load configuration
        config = load_workflow_config(config_file)
        
        # Initialize manager
        manager = CalimeroManager()
        
        # Create workflow executor
        executor = WorkflowExecutor(config, manager)
        
        # Execute workflow
        success = asyncio.run(executor.execute_workflow())
        
        if success:
            console.print("\n[bold green]🎉 Workflow completed successfully![/bold green]")
            if verbose and executor.workflow_results:
                console.print("\n[bold]Workflow Results:[/bold]")
                for key, value in executor.workflow_results.items():
                    console.print(f"  {key}: {value}")
        else:
            console.print("\n[bold red]❌ Workflow failed![/bold red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[red]Failed to execute workflow: {str(e)}[/red]")
        sys.exit(1)
