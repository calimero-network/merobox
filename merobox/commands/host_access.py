"""What a workflow YAML may reach on the host that runs it."""

import os
from typing import Any, Optional

from merobox.commands.constants import DEFAULT_IMAGE, SAFE_NAME_RE

TRUST_WORKFLOW_FLAG = "--trust-workflow"
ROOT_NOT_GIVEN = object()  # a step built without a root refuses host paths
TRUSTED_IMAGE_PREFIX = DEFAULT_IMAGE.rsplit("/", 1)[0] + "/"  # Calimero's own images
STEP_HOST_PATHS = {  # step keys naming a host path, opened from the cwd
    "upload_blob": ("file_path",),
    "download_blob": ("output_path",),
    "install_application": ("path",),
    "node_exec": ("data_dir",),
    "script": ("script",),
}
STEP_WRITE_PATHS = {("download_blob", "output_path")}  # step keys merobox writes to
GIT_CONTROL_NAMES = {".git", ".gitmodules", ".gitattributes", ".gitconfig"}  # run code
GIT_LAYOUT_FILES = {"head", "config", "packed-refs"}  # written, these make a git dir
GIT_LAYOUT_DIRS = {"objects", "refs", "hooks"}  # whose config git obeys


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


def require_safe_node_name(name: Any) -> None:
    """Raise ValueError unless `name` is one path component; a node's name names
    its data dir, log and pid files, so this holds whatever the workflow is trusted with.
    """
    if not SAFE_NAME_RE.match(str(name)):
        raise ValueError(f"unsafe node name {name!r}")


def strip_literal_quotes(value: Any) -> Any:
    """`'x'` or `"x"` -> `x`, as steps read a quoted literal; placeholders stay."""
    if (
        isinstance(value, str)
        and len(value) >= 2
        and value[0] == value[-1]
        and value[0] in "'\""
        and "{{" not in value
        and "}}" not in value
    ):
        return value[1:-1]
    return value


def check_host_access(
    config: dict[str, Any],
    workflow_dir: str,
    *,
    cli_no_docker: bool = False,
    cli_binary_path: bool = False,
) -> None:
    """Raise ValueError naming every YAML key that needs --trust-workflow; keys
    the CLI already overrides are the runner's choice, not the YAML's."""
    refused = [
        *_host_process_refusals(config, cli_no_docker, cli_binary_path),
        *_remote_refusals(config),
        *_image_refusals(config),
        *_path_refusals(config, workflow_dir),
    ]
    if refused:
        raise ValueError(
            "This workflow asks for host access that needs your consent; "
            f"rerun with {TRUST_WORKFLOW_FLAG} if you trust it:\n  - "
            + "\n  - ".join(refused)
        )


def _host_process_refusals(
    config: dict[str, Any], cli_no_docker: bool, cli_binary_path: bool
) -> list[str]:
    refused = []
    # --binary-path on the CLI is the runner choosing a host merod already.
    if config.get("no_docker") and not (cli_no_docker or cli_binary_path):
        refused.append("no_docker: runs merod as a host process")
    if config.get("binary_path") and not cli_binary_path:
        refused.append(f"binary_path: runs the host binary '{config['binary_path']}'")
    nodes = config.get("nodes")
    # In binary mode these flags reach a host merod; any of them may bind or write.
    binary_mode = cli_no_docker or config.get("no_docker")
    if binary_mode:
        if isinstance(nodes, dict) and nodes.get("init_args"):
            refused.append("nodes.init_args: passes flags to merod on the host")
        refused += [
            f"{where}.config_path: hands merod on the host a whole config.toml"
            for where, scope in _node_scopes(nodes)
            if scope.get("config_path")
        ]
        refused += [
            f"step '{step.get('name', step['type'])}' node_exec: runs merod on the "
            "host with the step's arguments"
            for step in _steps(config.get("steps"))
            if step["type"] == "node_exec"
        ]
    refused += [
        f"step '{step.get('name', step['type'])}' target local: runs a script "
        "as a host process"
        for step in _steps(config.get("steps"))
        if step["type"] == "script" and step.get("target") == "local"
    ]
    return refused


def _remote_refusals(config: dict[str, Any]) -> list[str]:
    if not config.get("remote_nodes"):
        return []
    return [
        "remote_nodes: sends requests, files and ${ENV} credentials to "
        f"{', '.join(map(str, config['remote_nodes']))}, and saves them to "
        "your remote node registry"
    ]


def _image_refusals(config: dict[str, Any]) -> list[str]:
    """Images the workflow picks for containers merobox runs as root."""
    topology = config.get("topology")
    boot_node = topology.get("boot_node") if isinstance(topology, dict) else None
    images = [
        (f"{where}.image", scope.get("image"))
        for where, scope in _node_scopes(config.get("nodes"))
    ]
    images += [
        (f"step '{step.get('name', step['type'])}' image", step.get("image"))
        for step in _steps(config.get("steps"))
    ]
    images += [
        ("auth_image", config.get("auth_image")),
        (
            "topology.boot_node.image",
            boot_node.get("image") if isinstance(boot_node, dict) else None,
        ),
    ]
    return [
        f"{where}: runs '{image}', which is not a Calimero image "
        f"({TRUSTED_IMAGE_PREFIX}*)"
        for where, image in images
        if image and not str(image).startswith(TRUSTED_IMAGE_PREFIX)
    ]


def _path_refusals(config: dict[str, Any], workflow_dir: str) -> list[str]:
    root = workflow_root(workflow_dir)
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
    refused = []
    for where, scope, paths in scopes:
        is_step = where.startswith("step ")
        for key, relative_to in paths.items():
            path = strip_literal_quotes(scope.get(key))
            # A step re-checks its paths once placeholders resolve.
            if is_step and "{{" in str(path):
                continue
            writes = (scope.get("type"), key) in STEP_WRITE_PATHS
            mounts = key == "data_dir"
            reason = host_path_refusal(path, root, relative_to, writes, mounts)
            if reason:
                refused.append(f"{where} {key}: {reason}")
    return refused


def workflow_root(workflow_dir: str) -> str:
    """The directory YAML host paths must stay in: the git checkout holding the
    workflow below $HOME, else the workflow's own directory (never the cwd)."""
    home = os.path.realpath(os.path.expanduser("~"))
    current = os.path.realpath(workflow_dir)
    while current != home:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return workflow_dir


def _holds_home(root: str) -> bool:
    home = os.path.realpath(os.path.expanduser("~"))
    real_root = os.path.realpath(root)
    return home == real_root or home.startswith(real_root.rstrip(os.sep) + os.sep)


def host_path_refusal(
    path: Any, root: str, relative_to: str, writes: bool = False, mounts: bool = False
) -> Optional[str]:
    """Why `path`, opened from `relative_to` (written if `writes`, bind-mounted if
    `mounts`), may not be used under `root`; None if it may."""
    if path is None:
        return None
    path = os.path.expanduser(str(path))
    if "{{" in path:
        return f"'{path}' is only known at run time"
    if _holds_home(root):
        return f"'{path}' cannot be confined: {root} holds your home directory"
    joined = os.path.join(relative_to, path)
    ok, _ = validate_path_within(joined, root, allow_parent_refs=True)
    if not ok:
        return f"'{path}' is outside {root}"
    inside = os.path.relpath(os.path.realpath(joined), os.path.realpath(root))
    if mounts and inside == os.curdir:
        return f"'{path}' would mount the whole of {root}, .git included"
    reason = git_path_refusal(inside.split(os.sep), writes)
    return reason and f"'{path}' {reason}"


def git_path_refusal(parts: list[str], writes: bool) -> Optional[str]:
    """Why the path made of `parts` must not be used (written if `writes`) inside
    a checkout; None if it may. Git runs code from what these names hold."""
    parts = [part.lower() for part in parts]  # case-insensitive filesystems
    if GIT_CONTROL_NAMES.intersection(parts):
        return "is a git control file or inside .git"
    if writes and (
        parts[-1] in GIT_LAYOUT_FILES or GIT_LAYOUT_DIRS.intersection(parts[:-1])
    ):
        return "could plant a git directory"
    return None


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
