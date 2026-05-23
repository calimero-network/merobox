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

# Modern multi-node clusters (run_multiple_nodes path) attach nodes to this
# user-defined bridge. Preferred over `bridge` when both are present.
CLUSTER_NETWORK = "merobox-cluster"
# Universal Docker default — always exists, always a safe fallback target
# for a re-attach when no better signal is available.
DEFAULT_NETWORK = "bridge"
# Auth-mode networks. `calimero_web` carries the user-facing libp2p / RPC
# traffic that a partition test wants to sever; `calimero_internal` is the
# Traefik-↔-node backend and is the WRONG target for a peer partition.
# When both are attached, prefer web (severing it also makes the node
# unreachable from peers via the routed path).
_AUTH_WEB_NETWORK = "calimero_web"
_AUTH_INTERNAL_NETWORK = "calimero_internal"
# Internal dynamic-values key prefix. Use partition_network_key(node) to
# build the per-node key; the helper guarantees disconnect_node and
# connect_node format it the same way (a raw f-string template would
# silently break with a typo).
_PARTITION_NETWORK_KEY_PREFIX = "__partition_network_"


def partition_network_key(node: str) -> str:
    """Return the dynamic_values key disconnect_node uses to record the
    network it severed, so connect_node can reattach to the same one."""
    return f"{_PARTITION_NETWORK_KEY_PREFIX}{node}"


# Networks that are never valid partition targets even if listed on a
# container — `host` shares the host stack; `none` is the absence of a NIC.
_SKIP_NETWORKS = frozenset({"host", "none"})
# TOML key match for mdns. Tolerates the formatting variants TOML allows
# (whitespace, case) so a stylistic difference in someone's config.toml
# can't silently suppress the relay-bypass warning.
_MDNS_FALSE_RE = re.compile(r"(?im)^\s*mdns\s*=\s*false\s*(?:#.*)?$")
# Same restriction merobox's manager applies to node names. Used here to
# guard node-name → container-path interpolation (e.g. warn_if_mdns_enabled
# reads /app/data/<node_name>/config.toml). A crafted name like
# `../../etc` would otherwise read arbitrary files inside the container.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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
    manager here is a programmer error — surfaces as a clear RuntimeError
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
    `merobox-cluster` user-defined bridge (count >= 2 + restart non-auth
    path in run_multiple_nodes), or `calimero_web` + `calimero_internal`
    (auth-mode). The right target is whatever the container is actually
    attached to.

    Priority:
      1. `merobox-cluster` if attached — the dominant modern case.
      2. `calimero_web` if attached — the user-facing auth-mode network.
         Severing it is what a partition test wants;
         `calimero_internal` is the Traefik backend channel and would
         leave peers reachable via the routed path.
      3. The single non-default attached network — custom-network case.
      4. The alphabetically-first attached candidate when multiple non-
         special networks remain, with a warning so the author can pin
         `network:` explicitly.
      5. `bridge` — universal Docker default; the safe fallback when the
         container has no attached networks (e.g. mid-partition reattach
         without a preceding disconnect_node to inform connect_node).
    """
    try:
        container.reload()
    except Exception as exc:
        # Stale attrs would silently pick the wrong network; warn so the
        # author at least sees the cause in the run log.
        console.print(
            f"[yellow]⚠️  container.reload() failed while detecting network "
            f"({exc!r}); proceeding with possibly stale NetworkSettings.[/yellow]"
        )
    networks_dict = container.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
    candidates = [n for n in networks_dict.keys() if n not in _SKIP_NETWORKS]

    if CLUSTER_NETWORK in candidates:
        return CLUSTER_NETWORK
    if _AUTH_WEB_NETWORK in candidates:
        return _AUTH_WEB_NETWORK
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Ambiguous multi-network attachment — pick the first sorted
        # candidate so the choice is at least deterministic and known-attached
        # (defaulting to `bridge` here would be wrong for any workflow that
        # put nodes on custom networks but not bridge).
        sorted_candidates = sorted(candidates)
        chosen = sorted_candidates[0]
        console.print(
            f"[yellow]⚠️  Container attached to multiple networks "
            f"({', '.join(sorted_candidates)}); picking `{chosen}` for the "
            f"partition. Pin `network:` in the step to override.[/yellow]"
        )
        return chosen
    # No candidates (fully disconnected, or attached only to host/none).
    # connect_node short-circuits this via the partition-network dynamic
    # value for the common disconnect→connect round-trip; bridge is the
    # safe Docker-wide default for the residual case.
    return DEFAULT_NETWORK


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
    # per-node config at $CALIMERO_HOME/<node_name>/config.toml. Validate
    # node_name against the same safe-name pattern manager uses, so a crafted
    # workflow value can't path-traverse out of the data dir.
    if not _SAFE_NAME_RE.match(node_name):
        return

    # exec_run blocks indefinitely on a paused container (the process is
    # SIGSTOP'd and never reads from the exec stream). Check state first so
    # disconnect/fault steps running after a pause_container don't hang
    # the workflow on a best-effort warning.
    try:
        container.reload()
        state = container.attrs.get("State", {}).get("Status")
    except Exception:
        state = None
    if state and state != "running":
        # Log so an author who paused-then-disconnected a node still sees
        # *something* in the run log — silently skipping would mask the
        # case where a workflow author actually wanted the warning.
        console.print(
            f"[dim]({node_name}: container state is '{state}', skipping "
            f"mdns check — paused/exited exec_run would hang)[/dim]"
        )
        return

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
