#!/usr/bin/env python3
"""
Merobox CLI
A Python CLI tool for managing Calimero nodes in Docker containers.
"""

import click

from merobox import __version__
from merobox.commands import (
    application,
    blob,
    bootstrap,
    call,
    context,
    health,
    identity,
    install,
    join,
    list,
    logs,
    nuke,
    proposals,
    remote,
    run,
    stop,
)


@click.group()
@click.version_option(version=__version__)
def cli():
    """Merobox CLI - Manage Calimero nodes in Docker containers."""
    pass


# Add commands to the CLI group
cli.add_command(run)
cli.add_command(stop)
cli.add_command(list)
cli.add_command(logs)
cli.add_command(health)
cli.add_command(install)
cli.add_command(application)
cli.add_command(nuke)
cli.add_command(identity)
cli.add_command(context)
cli.add_command(join)
cli.add_command(call)
cli.add_command(blob)
cli.add_command(proposals)
cli.add_command(bootstrap)
cli.add_command(remote)


def main():
    """Main entry point for the merobox CLI."""
    cli()


if __name__ == "__main__":
    main()
