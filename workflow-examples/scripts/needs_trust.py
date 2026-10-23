"""Exit 0 if a workflow needs --trust-workflow in this lane, 1 if it does not.

Usage: needs_trust.py <workflow_file> [--no-docker]
"""

import os
import sys

from merobox.commands.bootstrap.config import load_workflow_config
from merobox.commands.host_access import check_host_access


def main(workflow_file: str, *lane: str) -> int:
    config = load_workflow_config(workflow_file)
    try:
        check_host_access(
            config,
            os.path.dirname(os.path.abspath(workflow_file)),
            cli_no_docker="--no-docker" in lane,
        )
    except ValueError as refusal:
        print(refusal, file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
