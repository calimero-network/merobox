"""Shared helpers for Docker-driven workflow steps (pause/restart/network/fault).

All helpers operate on the merobox DockerManager that the workflow executor
passes in and emit consistent rich-console output, so individual step files
stay focused on the operation they wrap rather than client wiring.
"""

from __future__ import annotations

import re
from typing import Any

import docker.errors
from rich.markup import escape

from merobox.commands.utils import console

# Modern multi-node clusters attach nodes to this user-defined bridge.
# disconnect_node / connect_node fall back to it when the workflow doesn't
# pin a `network:` and the container exposes no other usable network (e.g.
# when ConnectNodeStep runs after a full disconnect).
CLUSTER_NETWORK = "merobox-cluster"
# Networks that are never valid partition targets even if listed on a
# container — `host` shares the host stack; `none` is the absence of a NIC.
_SKIP_NETWORKS = frozenset({"host", "none"})
# TOML key match for mdns. Tolerates the formatting variants TOML allows
# (whitespace, case) so a stylistic difference in someone's config.toml
# can't silently suppress the relay-bypass warning.
_MDNS_FALSE_RE = re.compile(r"(?im)^\s*mdns\s*=\s*false\s*(?:#.*)?$")


def is_binary_mode(manager: Any) -> bool:
    """True when the workflow is running merod-as-binary (no Docker)."""
    return (
        manager is not None
        and hasattr(manager, "binary_path")
        and manager.binary_path is not None
    )


def get_docker_client(manager: Any):
    """Return the docker client from the executor-provided manager.

    Steps that consume Docker primitives are expected to short-circuit on
    is_binary_mode before reaching this helper, so a missing or binary-mode
    manager here is a programmer error — surfaces as a clear AttributeError
    rather than silently spinning up a fresh DockerManager (which would
    register signal handlers and connect to docker.sock as a side effect).
    """
    if manager is None or is_binary_mode(manager):
        raise RuntimeError(
            "Docker-mode step reached get_docker_client without a "
            "DockerManager — caller must short-circuit on is_binary_mode."
        )
    return manager.client


def resolve_container(manager: Any, container_name: str) -> Any | None:
    """Look up a Docker container by name; print diagnostics and return None on miss.

    Narrows the caught exception to docker.errors.NotFound so daemon-down or
    network-level failures propagate instead of being misreported as a
    missing container.
    """
    try:
        return get_docker_client(manager).containers.get(container_name)
    except docker.errors.NotFound:
        console.print(f"[red]✗ Container '{container_name}' not found[/red]")
        return None


def detect_node_network(container: Any) -> str:
    """Pick the right Docker network for a partition/heal on this container.

    Workflows can run on Docker's default `bridge`, the modern
    `merobox-cluster` user-defined bridge (count >= 2 non-auth path), or
    `calimero_web` (auth-mode). disconnect_node/connect_node must target
    the one the container is actually on, or the step is a silent no-op.

    Priority:
      1. `merobox-cluster` if attached — the dominant modern case.
      2. The single non-default attached network — covers auth (calimero_web)
         and any custom-network workflow.
      3. `bridge` — legacy / single-node default.

    When called on a container that has been fully disconnected, returns
    CLUSTER_NETWORK so a subsequent connect step reattaches to the right
    bridge by default.
    """
    try:
        container.reload()
    except Exception:
        pass
    networks_dict = container.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
    candidates = [n for n in networks_dict.keys() if n not in _SKIP_NETWORKS]

    if CLUSTER_NETWORK in candidates:
        return CLUSTER_NETWORK
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        # Fully disconnected (or never attached to a usable network) — assume
        # the modern cluster default so connect_node reattaches sanely.
        return CLUSTER_NETWORK
    # Ambiguous (multiple non-default networks). bridge is the historical
    # default; surface a warning so the user can pin `network:` explicitly.
    console.print(
        f"[yellow]⚠️  Container attached to multiple networks "
        f"({', '.join(candidates)}); defaulting partition to `bridge`. "
        f"Pin `network:` in the step to override.[/yellow]"
    )
    return "bridge"


def safe_console_error(template: str, **fields: str) -> None:
    """Print a red error with all interpolated fields escaped against rich markup.

    Container stderr and Docker exception messages can contain text that
    looks like rich markup tags (`[bold]`, `[/red]`) or terminal escape
    sequences. Escaping at the interpolation boundary keeps the console
    output sound regardless of what the container or daemon produces.
    """
    escaped = {key: escape(str(value)) for key, value in fields.items()}
    console.print(f"[red]{template.format(**escaped)}[/red]")


def warn_if_mdns_enabled(container: Any, node_name: str) -> None:
    """Emit a yellow warning when a fault-injection step runs on a node with mDNS on.

    Relay-recovery code paths can be bypassed when peers on the same bridge
    find each other via mDNS — workflows that exercise those paths should set
    `mdns: false` in the node config. We read the live config.toml from
    inside the container rather than the host-side path so the check stays
    accurate even with custom data_dir setups.

    The warning fires unless the config contains an explicit `mdns = false`
    line. Both `mdns = true` and "no mdns setting" (merod's default is true)
    produce the warning — silence requires opt-in, since the cost of a
    silently-passing relay test outweighs the cost of a false alarm.
    """
    # CALIMERO_HOME is /app/data inside the container, and merod stores the
    # per-node config at $CALIMERO_HOME/<node_name>/config.toml. Use the exact
    # path so the check doesn't traverse anything else under /app/data.
    config_path = f"/app/data/{node_name}/config.toml"
    try:
        result = container.exec_run(["cat", config_path])
        if result.exit_code != 0:
            return
        text = result.output.decode("utf-8", errors="replace")
    except Exception:
        return

    if _MDNS_FALSE_RE.search(text):
        return

    console.print(
        f"[yellow]⚠️  {node_name}: discovery.mdns is enabled — relay/rendezvous "
        f"code paths may not be exercised. Set `mdns: false` in nodes config "
        f"to make this fault test meaningful.[/yellow]"
    )
