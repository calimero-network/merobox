#!/usr/bin/env python3
"""
Merobox CLI
Run workflows against release-v2 (Calimero Cloud) clusters of merod nodes.
"""

import sys

import click

from merobox import __version__
from merobox.commands import bootstrap, cloud, run


@click.group()
@click.version_option(version=__version__)
def cli():
    """Merobox CLI - run workflows against release-v2 merod clusters."""
    pass


cli.add_command(cloud)
# v1's names for running and checking a workflow, routed to `cloud`.
cli.add_command(bootstrap)
cli.add_command(run)


def _use_utf8_stdio() -> None:
    """Make stdout and stderr able to carry the output merobox actually writes.

    Windows defaults them to the ANSI code page — cp1252 on most installs —
    which cannot encode the emoji in merobox's own progress output. The result
    is not mangled text but a crash: `console.print("\U0001f680 Executing
    Workflow: ...")` raises `UnicodeEncodeError` before a single node starts,
    from the banner rather than from anything the workflow did.

    Done here, at the entry point, so no caller has to know. Setting
    `PYTHONIOENCODING` in the environment works too, and asking every CI job and
    every Windows user to remember that is the bug, not the fix.

    `errors="replace"` is deliberate: a character that still cannot be encoded
    should degrade to `?` rather than take the process down. Losing a glyph is
    not worth failing a workflow over.

    A no-op where the encoding is already UTF-8, which is every POSIX host.
    """
    for stream in (sys.stdout, sys.stderr):
        # Not a TextIOWrapper when redirected or captured (pytest, some
        # embeddings), and those have no encoding to reconfigure.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Best effort. A stream that refuses reconfiguration is not a
            # reason to refuse to run.
            pass


def main():
    """Main entry point for the merobox CLI."""
    _use_utf8_stdio()
    cli()


if __name__ == "__main__":
    main()
