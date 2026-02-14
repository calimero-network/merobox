"""
Commands module - All available CLI commands.
"""

from merobox.commands import near
from merobox.commands.application import application
from merobox.commands.blob import blob
from merobox.commands.bootstrap import bootstrap
from merobox.commands.call import call
from merobox.commands.context import context
from merobox.commands.errors import (
    AuthenticationError,
    AuthError,
    ClientError,
    ConfigurationError,
    MeroboxError,
    MeroboxTimeoutError,
    NodeError,
    NodeResolutionError,
    StepExecutionError,
    StepValidationError,
    TimeoutError,
    ValidationError,
    WorkflowError,
)
from merobox.commands.health import health
from merobox.commands.identity import identity
from merobox.commands.install import install
from merobox.commands.join import join
from merobox.commands.list import list
from merobox.commands.logs import logs
from merobox.commands.manager import DockerManager
from merobox.commands.nuke import nuke
from merobox.commands.proposals import proposals
from merobox.commands.remote import remote
from merobox.commands.run import run
from merobox.commands.stop import stop

__all__ = [
    # Commands
    "DockerManager",
    "run",
    "stop",
    "list",
    "logs",
    "health",
    "install",
    "application",
    "nuke",
    "identity",
    "context",
    "bootstrap",
    "call",
    "blob",
    "join",
    "proposals",
    "remote",
    "near",
    # Error classes
    "MeroboxError",
    "NodeError",
    "NodeResolutionError",
    "AuthError",
    "AuthenticationError",
    "WorkflowError",
    "StepValidationError",
    "StepExecutionError",
    "ValidationError",
    "ClientError",
    "MeroboxTimeoutError",
    "TimeoutError",  # Backward compatibility alias for MeroboxTimeoutError
    "ConfigurationError",
]
