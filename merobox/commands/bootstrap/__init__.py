"""
Bootstrap command package - Automate Calimero node workflows using YAML configuration files.

This package provides:
- bootstrap: Main CLI command with run/validate/create-sample subcommands
- Workflow execution engine
- Step-by-step workflow processing
- Configuration validation
"""

from .bootstrap import bootstrap

__all__ = ['bootstrap']
