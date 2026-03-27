"""
Join Open step executor - backward compatibility wrapper.

This module re-exports JoinContextStep as JoinOpenStep for backward compat.
All join logic is now consolidated in steps/join.py.
"""

from merobox.commands.bootstrap.steps.join import JoinContextStep as JoinOpenStep

__all__ = ["JoinOpenStep"]
