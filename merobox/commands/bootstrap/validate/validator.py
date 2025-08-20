"""
Workflow configuration validator.

This module provides comprehensive validation for workflow configurations
without requiring full workflow execution.
"""

def validate_workflow_config(config: dict, verbose: bool = False) -> dict:
    """
    Validate a workflow configuration without executing it.
    
    Args:
        config: The workflow configuration dictionary
        verbose: Whether to show detailed validation information
    
    Returns:
        Dictionary with 'valid' boolean and 'errors' list
    """
    errors = []
    
    # Check required top-level fields
    required_fields = ['name', 'nodes', 'steps']
    for field in required_fields:
        if field not in config:
            errors.append(f"Missing required field: {field}")
    
    # Validate nodes configuration
    if 'nodes' in config:
        nodes = config['nodes']
        if not isinstance(nodes, dict):
            errors.append("'nodes' must be a dictionary")
        else:
            required_node_fields = ['chain_id', 'count', 'image', 'prefix']
            for field in required_node_fields:
                if field not in nodes:
                    errors.append(f"Missing required node field: {field}")
    
    # Validate steps configuration
    if 'steps' in config:
        steps = config['steps']
        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
        elif len(steps) == 0:
            errors.append("'steps' list cannot be empty")
        else:
            # Validate each step
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"Step {i+1} must be a dictionary")
                    continue
                
                step_name = step.get('name', f'Step {i+1}')
                step_type = step.get('type')
                
                if not step_type:
                    errors.append(f"Step '{step_name}' is missing 'type' field")
                    continue
                
                # Validate step-specific requirements
                step_errors = validate_step_config(step, step_name, step_type)
                errors.extend(step_errors)
    
    return {
        'valid': len(errors) == 0,
        'errors': errors
    }

def validate_step_config(step: dict, step_name: str, step_type: str) -> list:
    """
    Validate a single step configuration.
    
    Args:
        step: The step configuration dictionary
        step_name: Name of the step for error reporting
        step_type: Type of the step
    
    Returns:
        List of validation errors
    """
    errors = []
    
    # Import step classes dynamically to avoid circular imports
    try:
        if step_type == 'install_application':
            from ..steps.install import InstallApplicationStep
            step_class = InstallApplicationStep
        elif step_type == 'create_context':
            from ..steps.context import CreateContextStep
            step_class = CreateContextStep
        elif step_type == 'create_identity':
            from ..steps.identity import CreateIdentityStep
            step_class = CreateIdentityStep
        elif step_type == 'invite_identity':
            from ..steps.identity import InviteIdentityStep
            step_class = InviteIdentityStep
        elif step_type == 'join_context':
            from ..steps.join import JoinContextStep
            step_class = JoinContextStep
        elif step_type == 'call':
            from ..steps.execute import ExecuteStep
            step_class = ExecuteStep
        elif step_type == 'repeat':
            from ..steps.repeat import RepeatStep
            step_class = RepeatStep
        elif step_type == 'wait':
            from ..steps.wait import WaitStep
            step_class = WaitStep
        elif step_type == 'script':
            from ..steps.script import ScriptStep
            step_class = ScriptStep
        else:
            errors.append(f"Step '{step_name}' has unknown type: {step_type}")
            return errors
        
        # Create a temporary step instance to trigger validation
        # This will catch any validation errors without executing
        try:
            temp_step = step_class(step)
        except Exception as e:
            errors.append(f"Step '{step_name}' validation failed: {str(e)}")
            
    except ImportError as e:
        errors.append(f"Step '{step_name}' validation failed: Could not import step class for type '{step_type}': {str(e)}")
    
    return errors
