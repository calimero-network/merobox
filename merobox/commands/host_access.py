"""What a workflow YAML may reach on the host that runs it."""

import os
from typing import Any, Optional

from merobox.commands.constants import DEFAULT_IMAGE

TRUST_WORKFLOW_FLAG = "--trust-workflow"
TRUSTED_IMAGE_PREFIX = DEFAULT_IMAGE.rsplit("/", 1)[0] + "/"  # Calimero's own images
STEP_HOST_PATHS = {  # step keys naming a host path, opened from the cwd
    "upload_blob": ("file_path",),
    "download_blob": ("output_path",),
    "install_application": ("path",),
    "node_exec": ("data_dir",),
}


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
    if config.get("remote_nodes"):
        refused.append(
            "remote_nodes: sends requests, files and ${ENV} credentials to "
            f"{', '.join(map(str, config['remote_nodes']))}, and saves them to "
            "your remote node registry"
        )
    cwd = os.getcwd()
    # Each scope: where it sits, its keys, and where merobox resolves its paths from.
    scopes = [
        (where, scope, {"data_dir": cwd, "config_path": workflow_dir})
        for where, scope in _node_scopes(config.get("nodes"))
    ] + [
        (
            f"step '{step.get('name', step['type'])}'",
            step,
            dict.fromkeys(STEP_HOST_PATHS.get(step["type"], ()), cwd),
        )
        for step in _steps(config.get("steps"))
    ]
    for where, scope, paths in scopes:
        image = scope.get("image")
        if image and not str(image).startswith(TRUSTED_IMAGE_PREFIX):
            refused.append(
                f"{where} image: runs '{image}' as root with a host directory "
                f"mounted (only {TRUSTED_IMAGE_PREFIX}* images are trusted)"
            )
        for key, relative_to in paths.items():
            reason = _outside(scope.get(key), workflow_dir, relative_to)
            if reason:
                refused.append(f"{where} {key}: {reason}")
    if refused:
        raise ValueError(
            "This workflow asks for access to the host outside its directory; "
            f"rerun with {TRUST_WORKFLOW_FLAG} if you trust it:\n  - "
            + "\n  - ".join(refused)
        )


def _outside(path: Any, workflow_dir: str, relative_to: str) -> Optional[str]:
    """Why `path`, resolved from `relative_to`, is not inside `workflow_dir`; None
    if it is. A placeholder resolves at run time, so it cannot be checked."""
    if path is None:
        return None
    path = os.path.expanduser(str(path))
    if "{{" in path:
        return f"'{path}' is only known at run time"
    ok, _ = validate_path_within(os.path.join(relative_to, path), workflow_dir)
    return None if ok else f"'{path}' is outside the workflow directory {workflow_dir}"


def _node_scopes(nodes: Any):
    """The nodes block and each per-node block, with where each sits."""
    if not isinstance(nodes, dict):
        return
    yield "nodes", nodes
    for name, scope in nodes.items():
        if isinstance(scope, dict):
            yield f"nodes.{name}", scope


def _steps(value: Any):
    """Every step under `value`, including those nested in parallel and repeat."""
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            yield value
        for nested in value.values():
            yield from _steps(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _steps(nested)
