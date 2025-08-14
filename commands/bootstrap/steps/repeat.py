"""
Repeat step executor for executing nested steps multiple times.
"""

from typing import Dict, Any
from ...utils import console
from .base import BaseStep
from .install import InstallApplicationStep
from .context import CreateContextStep
from .identity import CreateIdentityStep, InviteIdentityStep
from .join import JoinContextStep
from .execute import ExecuteStep
from .wait import WaitStep


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
