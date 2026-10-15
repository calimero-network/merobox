"""What a workflow YAML may reach on the host that runs it."""

import os
from typing import Any

TRUST_WORKFLOW_FLAG = "--trust-workflow"


def validate_path_within(path: str, allowed_dir: str) -> tuple[bool, str]:
    """Check that `path` has no '..' component and resolves inside `allowed_dir`.

    A relative `path` resolves against `allowed_dir`. Returns (is_valid, error).
    """
    if not path:
        return False, "Path is empty"

    # Normalize separators for cross-platform check
    path_parts = path.replace("\\", "/").split("/")
    if ".." in path_parts:
        return False, f"Path traversal detected: path contains '..': {path}"

    try:
        absolute_path = os.path.realpath(os.path.join(allowed_dir, path))
    except (OSError, ValueError) as e:
        return False, f"Failed to resolve path: {e}"

    try:
        allowed_normalized = os.path.normcase(os.path.realpath(allowed_dir))
        path_normalized = os.path.normcase(absolute_path)
        if (
            not path_normalized.startswith(allowed_normalized + os.sep)
            and path_normalized != allowed_normalized
        ):
            return False, (
                f"Path traversal detected: path '{path}' resolves to "
                f"'{absolute_path}' which is outside the allowed directory "
                f"'{allowed_dir}'"
            )
    except ValueError as e:
        return False, f"Path validation failed: {e}"

    return True, ""


def check_host_access(
    config: dict[str, Any],
    workflow_dir: str,
    *,
    cli_no_docker: bool = False,
    cli_binary_path: bool = False,
) -> None:
    """Raise ValueError naming every YAML key that reaches the host outside
    `workflow_dir`. Keys the CLI already overrides are not the YAML's choice."""
    refused = []
    if config.get("no_docker") and not cli_no_docker:
        refused.append("no_docker: runs merod as a host process")
    if config.get("binary_path") and not cli_binary_path:
        refused.append(f"binary_path: runs the host binary '{config['binary_path']}'")
    if refused:
        raise ValueError(
            "This workflow asks for access to the host outside its directory; "
            f"rerun with {TRUST_WORKFLOW_FLAG} if you trust it:\n  - "
            + "\n  - ".join(refused)
        )
