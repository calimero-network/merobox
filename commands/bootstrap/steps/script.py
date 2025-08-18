"""
Script execution step for bootstrap workflow.
"""

import os
import time
import tarfile
import io
from typing import Dict, Any
from .base import BaseStep
from ...utils import console


class ScriptStep(BaseStep):
    """Execute a script on Docker images or running nodes."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.script_path = config.get('script')
        self.target = config.get('target', 'image')  # 'image' or 'nodes'
        self.description = config.get('description', f'Execute script: {self.script_path}')
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        """Execute the script step."""
        if not self.script_path:
            console.print("[red]❌ Script path not specified[/red]")
            return False
        
        if not os.path.exists(self.script_path):
            console.print(f"[red]❌ Script file not found: {self.script_path}[/red]")
            return False
        
        console.print(f"\n[bold blue]📜 {self.description}[/bold blue]")
        
        if self.target == 'image':
            return await self._execute_on_image(workflow_results, dynamic_values)
        elif self.target == 'nodes':
            return await self._execute_on_nodes(workflow_results, dynamic_values)
        else:
            console.print(f"[red]❌ Unknown target type: {self.target}[/red]")
            return False
    
    async def _execute_on_image(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        """Execute script on a Docker image before starting nodes."""
        try:
            console.print(f"[yellow]Executing script on Docker image: {self.script_path}[/yellow]")
            
            # Read the script content
            try:
                with open(self.script_path, 'r') as file:
                    script_content = file.read()
            except Exception as e:
                console.print(f"[red]Failed to read script file: {str(e)}[/red]")
                return False
            
            # Get the base image from the workflow context
            # We'll use a default image since we don't have direct access to the config here
            image = 'ghcr.io/calimero-network/merod:edge'
            
            console.print(f"[cyan]Using Docker image: {image}[/cyan]")
            
            # Create a temporary container to execute the script
            temp_container_name = f"script-step-{int(time.time())}"
            
            try:
                # Create container with the script mounted
                try:
                    from ...manager import CalimeroManager
                    manager = CalimeroManager()
                    
                    container = manager.client.containers.run(
                        name=temp_container_name,
                        image=image,
                        detach=True,
                        entrypoint="",  # Override the merod entrypoint
                        command=["sh", "-c", "while true; do sleep 1; done"],  # Keep container running
                        volumes={
                            os.path.abspath(self.script_path): {'bind': '/tmp/script.sh', 'mode': 'ro'}
                        },
                        working_dir='/tmp'
                    )
                except Exception as create_error:
                    console.print(f"[red]Failed to create container: {str(create_error)}[/red]")
                    return False
                
                # Wait for container to be ready
                time.sleep(2)
                container.reload()
                
                if container.status != 'running':
                    console.print(f"[red]Failed to start temporary container for script[/red]")
                    console.print(f"[red]Container status: {container.status}[/red]")
                    try:
                        logs = container.logs().decode('utf-8')
                        if logs.strip():
                            console.print(f"[red]Container logs: {logs}[/red]")
                    except:
                        pass
                    try:
                        container.remove()
                    except:
                        pass
                    return False
                
                # Make script executable and run it
                console.print("[cyan]Running script in container...[/cyan]")
                
                result = container.exec_run(["chmod", "+x", "/tmp/script.sh"])
                if result.exit_code != 0:
                    console.print(f"[yellow]Warning: Could not make script executable: {result.output.decode()}[/yellow]")
                
                result = container.exec_run(["/bin/sh", "/tmp/script.sh"])
                
                output = result.output.decode('utf-8', errors='replace')
                if output.strip():
                    console.print("[cyan]Script output:[/cyan]")
                    console.print(output)
                
                if result.exit_code != 0:
                    console.print(f"[red]Script failed with exit code: {result.exit_code}[/red]")
                    return False
                
                console.print("[green]✓ Script executed successfully[/green]")
                return True
                
            finally:
                try:
                    container.stop(timeout=5)
                    container.remove()
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not clean up temporary container: {str(e)}[/yellow]")
            
        except Exception as e:
            console.print(f"[red]Failed to execute script: {str(e)}[/red]")
            return False
    
    async def _execute_on_nodes(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        """Execute script on all running Calimero nodes."""
        try:
            console.print(f"[yellow]Executing script on all running nodes: {self.script_path}[/yellow]")
            
            # Read the script content
            try:
                with open(self.script_path, 'r') as file:
                    script_content = file.read()
            except Exception as e:
                console.print(f"[red]Failed to read script file: {str(e)}[/red]")
                return False
            
            # Get all running Calimero nodes
            from ...manager import CalimeroManager
            manager = CalimeroManager()
            
            containers = manager.client.containers.list(
                filters={'label': 'calimero.node=true'}
            )
            
            if not containers:
                console.print("[yellow]No Calimero nodes are currently running[/yellow]")
                return True
            
            console.print(f"[cyan]Found {len(containers)} running nodes to execute script on[/cyan]")
            
            success_count = 0
            failed_nodes = []
            
            for container in containers:
                node_name = container.name
                console.print(f"\n[cyan]Executing script on {node_name}...[/cyan]")
                
                try:
                    # Copy the script to the container
                    script_name = f"script_{int(time.time())}.sh"
                    
                    # Create a temporary tar archive with the script
                    tar_buffer = io.BytesIO()
                    with tarfile.open(fileobj=tar_buffer, mode='w:tar') as tar:
                        # Create tarinfo for the script
                        tarinfo = tarfile.TarInfo(script_name)
                        tarinfo.size = len(script_content.encode('utf-8'))
                        tarinfo.mode = 0o755  # Executable permissions
                        
                        # Add the script to the tar archive
                        tar.addfile(tarinfo, io.BytesIO(script_content.encode('utf-8')))
                    
                    # Get the tar archive bytes
                    tar_data = tar_buffer.getvalue()
                    
                    try:
                        # Copy script to container using put_archive
                        container.put_archive('/tmp/', tar_data)
                        
                        # Make script executable
                        result = container.exec_run(["chmod", "+x", f"/tmp/{script_name}"])
                        if result.exit_code != 0:
                            console.print(f"[yellow]Warning: Could not make script executable on {node_name}: {result.output.decode()}[/yellow]")
                        
                        # Execute the script
                        result = container.exec_run(["/bin/sh", f"/tmp/{script_name}"])
                        
                        # Display script output
                        output = result.output.decode('utf-8', errors='replace')
                        if output.strip():
                            console.print(f"[cyan]Script output from {node_name}:[/cyan]")
                            console.print(output)
                        
                        # Check exit code
                        if result.exit_code != 0:
                            console.print(f"[red]Script failed on {node_name} with exit code: {result.exit_code}[/red]")
                            failed_nodes.append(node_name)
                        else:
                            console.print(f"[green]✓ Script executed successfully on {node_name}[/green]")
                            success_count += 1
                        
                        # Clean up script from container
                        try:
                            container.exec_run(["rm", f"/tmp/{script_name}"])
                        except:
                            pass
                        
                    finally:
                        # Clean up tar buffer
                        tar_buffer.close()
                            
                except Exception as e:
                    console.print(f"[red]Failed to execute script on {node_name}: {str(e)}[/red]")
                    failed_nodes.append(node_name)
            
            # Summary
            console.print(f"\n[bold]Script execution summary: {success_count}/{len(containers)} nodes successful[/bold]")
            
            if failed_nodes:
                console.print(f"[red]Failed on nodes: {', '.join(failed_nodes)}[/red]")
                return False
            
            console.print("[green]✓ Script executed successfully on all nodes[/green]")
            return True
            
        except Exception as e:
            console.print(f"[red]Failed to execute script on nodes: {str(e)}[/red]")
            return False
