#!/usr/bin/env python3
"""
Merobox CLI
A Python CLI tool for managing Calimero nodes in Docker containers.
"""

import click

from merobox import __version__
from merobox.commands import (
    bootstrap,
    health,
    logs,
    nuke,
    remote,
    run,
    stop,
)


@click.group()
@click.version_option(version=__version__)
def cli():
    """Merobox CLI - Manage Calimero nodes in Docker containers."""
    pass


# Node management and workflow commands only
cli.add_command(bootstrap)
cli.add_command(health)
cli.add_command(logs)
cli.add_command(nuke)
cli.add_command(remote)
cli.add_command(run)
cli.add_command(stop)


def main():
    """Main entry point for the merobox CLI."""
    cli()


if __name__ == "__main__":
    main()
