"""
Commands module - All available CLI commands.
"""

from merobox.commands.bootstrap import bootstrap
from merobox.commands.call import call
from merobox.commands.health import health
from merobox.commands.identity import identity
from merobox.commands.install import install
from merobox.commands.join import join
from merobox.commands.list import list
from merobox.commands.logs import logs
from merobox.commands.manager import CalimeroManager
from merobox.commands.nuke import nuke
from merobox.commands.run import run
from merobox.commands.stop import stop

__all__ = [
    "CalimeroManager",
    "run",
    "stop",
    "list",
    "logs",
    "health",
    "install",
    "nuke",
    "identity",
    "bootstrap",
    "call",
    "join",
]
