"""
Run module - Workflow execution functionality.

This module handles the execution of Calimero workflows including:
- Node management
- Step execution
- Dynamic variable resolution
- Result capture and export
"""

from .executor import WorkflowExecutor
from .run import run_workflow, run_workflow_sync

__all__ = ['WorkflowExecutor', 'run_workflow', 'run_workflow_sync']
