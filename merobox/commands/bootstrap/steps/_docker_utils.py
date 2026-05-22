"""Shared helpers for Docker-driven workflow steps (pause/restart/network/fault).

All helpers return Docker container objects via the merobox DockerManager and
emit consistent rich-console output, so individual step files stay focused on
the operation they wrap rather than client wiring.
"""

from __future__ import annotations

from typing import Any

from merobox.commands.utils import console


def is_binary_mode(manager: Any) -> bool:
    """True when the workflow is running merod-as-binary (no Docker)."""
    return (
        manager is not None
        and hasattr(manager, "binary_path")
        and manager.binary_path is not None
    )


def get_docker_manager(manager: Any):
    """Return a usable DockerManager — either the existing one or a new client."""
    if manager is not None and not is_binary_mode(manager):
        return manager
    from merobox.commands.manager import DockerManager

    return DockerManager()


def resolve_container(manager: Any, container_name: str) -> Any | None:
    """Look up a Docker container by name; print diagnostics and return None on miss.

    The caller is responsible for failing the step — we return None rather
    than raising so step output stays uniform with the rest of the codebase.
    """
    docker_manager = get_docker_manager(manager)
    try:
        return docker_manager.client.containers.get(container_name)
    except Exception as exc:
        console.print(f"[red]✗ Container '{container_name}' not found: {exc}[/red]")
        return None


def warn_if_mdns_enabled(container: Any, node_name: str) -> None:
    """Emit a yellow warning when a fault-injection step runs on a node with mDNS on.

    Relay-recovery code paths can be bypassed when peers on the same bridge
    find each other via mDNS — workflows that exercise those paths should set
    `mdns: false` in the node config. We read the live config.toml from
    inside the container rather than the host-side path so the check stays
    accurate even with custom data_dir setups.
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

    # Only warn when discovery.mdns is explicitly true. Absence ≈ default true
    # for merod today; we still warn so authors are aware.
    if "mdns = false" in text:
        return

    console.print(
        f"[yellow]⚠️  {node_name}: discovery.mdns is enabled — relay/rendezvous "
        f"code paths may not be exercised. Set `mdns: false` in nodes config "
        f"to make this fault test meaningful.[/yellow]"
    )
