"""Disconnect / connect step executors for the container's Docker network.

Used to simulate network partitions in tests. The container keeps running
and retains all in-memory state, but cannot reach (or be reached by) other
containers on its bridge network. Re-attach with connect_node.

Auto-targets the right network for the workflow: `merobox-cluster` for
multi-node (count >= 2) runs, `calimero_web` for auth-service workflows,
`bridge` for legacy / single-node setups. Override with explicit `network:`
if you need to disconnect from something specific (or from a network the
container is attached to alongside others).

Caveat: this is not a perfect partition. Containers also bind to the host's
exposed ports, so any peer connecting via host gateway would still see them.
Inside merobox's default 1-host setup this is fine — all inter-node libp2p
traffic flows over the bridge.

Reconnect typically needs a few seconds for libp2p mesh reformation
(heartbeats + peer discovery), so workflows should pair this with an
explicit `wait_for_sync` or short `wait` before asserting state propagation.
"""

from typing import Any

import docker.errors

from merobox.commands.bootstrap.steps._docker_utils import (
    detect_node_network,
    get_docker_client,
    is_binary_mode,
    partition_network_key,
    safe_console_error,
    warn_if_mdns_enabled,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class DisconnectNodeStep(BaseStep):
    """Disconnect a node container from its Docker network.

    Auto-detects the network from the container's NetworkSettings when
    `network:` is not set; falls back to merobox-cluster / bridge per
    detect_node_network's priority.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("network", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping disconnect_node: --no-docker mode has no "
                "Docker network to disconnect from[/yellow]"
            )
            return True

        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except docker.errors.NotFound:
            console.print(f"[red]✗ Container '{node_name}' not found[/red]")
            return False

        explicit_network = self.config.get("network")
        if explicit_network is not None:
            network_name = self._resolve_dynamic_value(
                explicit_network, workflow_results, dynamic_values
            )
        else:
            network_name = detect_node_network(container)

        warn_if_mdns_enabled(container, node_name)

        console.print(
            f"[yellow]Disconnecting {node_name} from network {network_name}...[/yellow]"
        )

        try:
            network = client.networks.get(network_name)
            network.disconnect(container)
        except Exception as exc:
            safe_console_error(
                "✗ Failed to disconnect {node} from {network}: {err}",
                node=node_name,
                network=network_name,
                err=exc,
            )
            return False

        # Record so a downstream connect_node reattaches to the SAME network,
        # which matters when the workflow path used run_node directly
        # (no merobox-cluster created) — auto-detection then has no signal.
        dynamic_values[partition_network_key(node_name)] = network_name

        console.print(f"[green]✓ Disconnected {node_name} from {network_name}[/green]")
        return True


class ConnectNodeStep(BaseStep):
    """Connect a node container back to a Docker network.

    Network resolution order when `network:` is not set:
      1. The network recorded by a preceding disconnect_node call (read
         from dynamic_values). This is the common case and the only
         signal that survives a full disconnect.
      2. detect_node_network on the container — if it's only partially
         disconnected, picks an attached candidate; otherwise falls back
         to Docker's default `bridge`.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("network", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping connect_node: --no-docker mode has no "
                "Docker network to connect to[/yellow]"
            )
            return True

        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except docker.errors.NotFound:
            console.print(f"[red]✗ Container '{node_name}' not found[/red]")
            return False

        explicit_network = self.config.get("network")
        if explicit_network is not None:
            network_name = self._resolve_dynamic_value(
                explicit_network, workflow_results, dynamic_values
            )
        else:
            # Prefer the network recorded by a prior disconnect_node — this is
            # the only signal that survives a full container disconnect, since
            # the container's NetworkSettings is empty by then.
            recorded = dynamic_values.get(partition_network_key(node_name))
            network_name = recorded or detect_node_network(container)

        console.print(
            f"[yellow]Connecting {node_name} to network {network_name}...[/yellow]"
        )

        try:
            network = client.networks.get(network_name)
            network.connect(container)
        except Exception as exc:
            safe_console_error(
                "✗ Failed to connect {node} to {network}: {err}",
                node=node_name,
                network=network_name,
                err=exc,
            )
            return False

        # Clean up the recorded partition network so a fresh disconnect
        # cycle doesn't pick up stale state.
        dynamic_values.pop(partition_network_key(node_name), None)

        console.print(f"[green]✓ Connected {node_name} to {network_name}[/green]")
        return True
