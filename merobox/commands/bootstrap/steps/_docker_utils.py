"""Shared helpers for Docker-driven workflow steps (pause/restart/network/fault).

All helpers operate on the merobox DockerManager that the workflow executor
passes in and emit consistent rich-console output, so individual step files
stay focused on the operation they wrap rather than client wiring.
"""

from __future__ import annotations

from typing import Any

import docker.errors

from merobox.commands.utils import console


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
    try:
        result = container.exec_run(
            ["sh", "-c", "cat /app/data/*/config.toml 2>/dev/null || true"]
        )
        if result.exit_code != 0:
            return
        text = result.output.decode("utf-8", errors="replace").lower()
    except Exception:
        return

    if "mdns = false" in text:
        return

    console.print(
        f"[yellow]⚠️  {node_name}: discovery.mdns is enabled — relay/rendezvous "
        f"code paths may not be exercised. Set `mdns: false` in nodes config "
        f"to make this fault test meaningful.[/yellow]"
    )
