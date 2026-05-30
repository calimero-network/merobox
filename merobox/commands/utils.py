"""
Shared utilities for Calimero CLI commands.
"""

import asyncio
import contextvars
import json
import logging
import os
import sys
from typing import Any, Optional

import docker
from rich import box
from rich.console import Console
from rich.table import Table

from merobox.commands.constants import DEFAULT_RPC_PORT, RPC_PORT_BINDING
from merobox.commands.manager import DockerManager

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Console verbosity control
#
# This governs how much merobox itself prints to the terminal. It is distinct
# from `--log-level`, which sets the merod *node* RUST_LOG level inside the
# container — do not conflate the two. Higher numeric level = more output.
#
# All gated output flows through `vprint()` so every bootstrap step benefits
# from a single knob (e.g. wait_for_sync suppressing its per-attempt blocks on
# the happy path while still showing the banner and final summary).
# ---------------------------------------------------------------------------
LOG_LEVEL_QUIET = 0  # essentials only: final summaries and errors
LOG_LEVEL_NORMAL = 1  # default: banners + summaries, no per-attempt chatter
LOG_LEVEL_VERBOSE = 2  # everything, including per-poll / per-attempt detail

# Accepted string spellings for MEROBOX_LOG_LEVEL / per-step verbose config.
_LOG_LEVEL_NAMES = {
    "quiet": LOG_LEVEL_QUIET,
    "q": LOG_LEVEL_QUIET,
    "normal": LOG_LEVEL_NORMAL,
    "default": LOG_LEVEL_NORMAL,
    "info": LOG_LEVEL_NORMAL,
    "verbose": LOG_LEVEL_VERBOSE,
    "v": LOG_LEVEL_VERBOSE,
    "debug": LOG_LEVEL_VERBOSE,
}

# Current verbosity, scoped per execution context. Set via `set_log_level()`
# (typically once at the start of `run_workflow`); read by `vprint()`. A
# ContextVar — rather than a plain module global — means a value set before
# spawning asyncio tasks is inherited by those tasks (each task copies the
# context at creation), yet concurrent branches (e.g. ParallelStep) or a
# library embedding can scope their own level without clobbering each other.
# Defaults to NORMAL so callers that never configure it get CI-friendly output.
_log_level_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "merobox_log_level", default=LOG_LEVEL_NORMAL
)


def parse_log_level(value: Any, default: int = LOG_LEVEL_NORMAL) -> int:
    """Map a string/int verbosity into a numeric level.

    Accepts the names in ``_LOG_LEVEL_NAMES`` (case-insensitive) or a numeric
    value (clamped to the valid range). Anything unrecognized falls back to
    ``default`` rather than raising, so a stray env var can never abort a run.

    A bool is the shorthand a per-step ``verbose:`` field yields:
    ``True`` -> VERBOSE, ``False`` -> NORMAL ("not verbose"). Both ignore
    ``default`` so the mapping is symmetric and independent of caller context.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        # bool is an int subclass, so check it before the int branch.
        return LOG_LEVEL_VERBOSE if value else LOG_LEVEL_NORMAL
    if isinstance(value, int):
        return max(LOG_LEVEL_QUIET, min(LOG_LEVEL_VERBOSE, value))
    name = str(value).strip().lower()
    if name.isdigit():
        return max(LOG_LEVEL_QUIET, min(LOG_LEVEL_VERBOSE, int(name)))
    return _LOG_LEVEL_NAMES.get(name, default)


def resolve_log_level(verbose: bool = False, quiet: bool = False) -> int:
    """Resolve effective console verbosity from flags and environment.

    Priority (highest first):
      1. Explicit ``--verbose`` / ``--quiet`` flags. ``--verbose`` wins if
         both are somehow set (louder is safer for debugging).
      2. ``MEROBOX_LOG_LEVEL`` env var (lets CI dial verbosity without
         editing workflow YAML).
      3. ``LOG_LEVEL_NORMAL`` default.
    """
    if verbose:
        return LOG_LEVEL_VERBOSE
    if quiet:
        return LOG_LEVEL_QUIET
    env = os.environ.get("MEROBOX_LOG_LEVEL")
    if env:
        return parse_log_level(env)
    return LOG_LEVEL_NORMAL


def set_log_level(level: int) -> None:
    """Set the console verbosity used by ``vprint()`` in the current context."""
    _log_level_var.set(level)


def get_log_level() -> int:
    """Return the current console verbosity for this context."""
    return _log_level_var.get()


def vprint(*args: Any, level: int = LOG_LEVEL_NORMAL, **kwargs: Any) -> None:
    """``console.print`` gated by verbosity.

    Prints only when the current level is at least ``level``. Use
    ``level=LOG_LEVEL_VERBOSE`` for per-attempt / debug chatter,
    ``level=LOG_LEVEL_NORMAL`` for banners, and plain ``console.print`` (or
    ``level=LOG_LEVEL_QUIET``) for output that must always appear, such as
    final success/failure summaries.
    """
    if _log_level_var.get() >= level:
        console.print(*args, **kwargs)


def _normalize_port(port_value: Any) -> Optional[int]:
    """Normalize an arbitrary port value into an integer if possible."""
    if isinstance(port_value, int):
        return port_value
    if isinstance(port_value, str) and port_value.isdigit():
        return int(port_value)
    return None


def get_node_rpc_url(node_name: str, manager: Any) -> str:
    """Get the RPC URL for a specific node."""
    host_port: Optional[int] = None

    if hasattr(manager, "get_node_rpc_port"):
        try:
            host_port = _normalize_port(manager.get_node_rpc_port(node_name))
        except Exception:
            host_port = None

    if host_port is None and hasattr(manager, "client"):
        try:
            container = manager.client.containers.get(node_name)
            container.reload()
            port_mappings = (
                container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            )
            host_bindings = port_mappings.get(RPC_PORT_BINDING) or []
            for binding in host_bindings:
                host_port = _normalize_port(binding.get("HostPort"))
                if host_port is not None:
                    break

            if host_port is None:
                port_bindings = (
                    container.attrs.get("HostConfig", {}).get("PortBindings") or {}
                )
                host_bindings = port_bindings.get(RPC_PORT_BINDING) or []
                for binding in host_bindings:
                    host_port = _normalize_port(binding.get("HostPort"))
                    if host_port is not None:
                        break
        except docker.errors.NotFound:
            logger.debug("Container %s not found when getting RPC URL", node_name)
            host_port = None
        except docker.errors.DockerException as e:
            logger.debug("Docker error getting RPC port for %s: %s", node_name, e)
            host_port = None
        except (KeyError, TypeError, AttributeError) as e:
            logger.debug("Error accessing container data for %s: %s", node_name, e)
            host_port = None

    if host_port is None:
        host_port = DEFAULT_RPC_PORT

    return f"http://localhost:{host_port}"


def check_node_running(node: str, manager: DockerManager) -> None:
    """Check if a node is running and exit if not."""
    try:
        container = manager.client.containers.get(node)
        if container.status != "running":
            console.print(f"[red]Node {node} is not running[/red]")
            sys.exit(1)
    except Exception:
        console.print(f"[red]Node {node} not found[/red]")
        sys.exit(1)


def run_async_function(func, *args, **kwargs) -> dict[str, Any]:
    """Helper to run async functions in sync context."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(func(*args, **kwargs))
        loop.close()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_generic_table(
    title: str, columns: list[tuple], data: list[dict[str, Any]]
) -> Table:
    """Create a generic table with specified columns and data."""
    table = Table(title=title, box=box.ROUNDED)

    for col_name, col_style in columns:
        table.add_column(col_name, style=col_style)

    for row_data in data:
        row_values = []
        for col_name, _ in columns:
            row_values.append(row_data.get(col_name, "Unknown"))
        table.add_row(*row_values)

    return table


def extract_nested_data(response_data: dict[str, Any], *keys) -> Any:
    """Extract data from nested dictionary using multiple possible key paths."""
    if not isinstance(response_data, dict):
        return None

    # Try direct key access first
    for key in keys:
        if key in response_data:
            return response_data[key]

    # Try nested data structure
    if "data" in response_data:
        data = response_data["data"]
        if isinstance(data, dict):
            for key in keys:
                if key in data:
                    return data[key]

    return None


def validate_port(port_str: str, port_name: str) -> int:
    """Validate and convert port string to integer."""
    try:
        port = int(port_str)
        if port < 1 or port > 65535:
            raise ValueError("Port must be between 1 and 65535")
        return port
    except ValueError as e:
        console.print(f"[red]Error: Invalid {port_name} '{port_str}'. {str(e)}[/red]")
        sys.exit(1)


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.1f} {size_names[i]}"


def safe_get(dictionary: dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get a value from a dictionary with a default fallback."""
    return dictionary.get(key, default) if isinstance(dictionary, dict) else default


def ensure_json_string(value: Any) -> str:
    """Ensure a value is a JSON string, converting if necessary."""
    if isinstance(value, str):
        # Try to parse to validate it's valid JSON
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            # If it's not valid JSON, treat it as a plain string and encode it
            return json.dumps(value)
    else:
        # Convert non-string values to JSON string
        return json.dumps(value)
