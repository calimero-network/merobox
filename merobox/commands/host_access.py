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


def validate_path_within(
    path: str, allowed_dir: str, allow_parent_refs: bool = False
) -> tuple[bool, str]:
    """Check that `path` resolves inside `allowed_dir`, and unless
    `allow_parent_refs` has no '..' component. Returns (is_valid, error)."""
    if not path:
        return False, "Path is empty"

    # Normalize separators for cross-platform check
    path_parts = path.replace("\\", "/").split("/")
    if ".." in path_parts and not allow_parent_refs:
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
    `workflow_root(workflow_dir)`. Keys the CLI already overrides are not the
    YAML's choice."""
    root = workflow_root(workflow_dir)
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
    nodes = config.get("nodes")
    # In binary mode these flags reach a host merod; any of them may bind or write.
    binary_mode = cli_no_docker or config.get("no_docker")
    if binary_mode and isinstance(nodes, dict) and nodes.get("init_args"):
        refused.append("nodes.init_args: passes flags to merod on the host")
    cwd = os.getcwd()
    # Each scope: where it sits, its keys, and where merobox resolves its paths from.
    scopes = [
        (where, scope, {"data_dir": cwd, "config_path": workflow_dir})
        for where, scope in _node_scopes(nodes)
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
            reason = host_path_refusal(scope.get(key), root, relative_to)
            if reason:
                refused.append(f"{where} {key}: {reason}")
    if refused:
        raise ValueError(
            "This workflow asks for access to the host outside its directory; "
            f"rerun with {TRUST_WORKFLOW_FLAG} if you trust it:\n  - "
            + "\n  - ".join(refused)
        )


def workflow_root(workflow_dir: str) -> str:
    """The directory YAML host paths must stay in: the git checkout holding the
    workflow, else the workflow's own directory (never the cwd, which may be $HOME)."""
    current = os.path.realpath(workflow_dir)
    while not os.path.exists(os.path.join(current, ".git")):
        parent = os.path.dirname(current)
        if parent == current:
            return workflow_dir
        current = parent
    return current


def host_path_refusal(path: Any, root: str, relative_to: str) -> Optional[str]:
    """Why `path`, opened from `relative_to`, is not inside `root`; None if it is.
    A placeholder resolves at run time, so it cannot be checked."""
    if path is None:
        return None
    path = os.path.expanduser(str(path))
    if "{{" in path:
        return f"'{path}' is only known at run time"
    ok, _ = validate_path_within(
        os.path.join(relative_to, path), root, allow_parent_refs=True
    )
    return None if ok else f"'{path}' is outside {root}"


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
