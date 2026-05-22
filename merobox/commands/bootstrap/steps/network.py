"""Disconnect / connect step executors for the Docker bridge network.

Used to simulate network partitions in tests. The container keeps running
and retains all in-memory state, but cannot reach (or be reached by) other
containers on the default bridge. Re-attach with connect_node.

Caveat: this is not a perfect partition. Containers also bind to the host's
exposed ports, so any peer connecting via host gateway would still see them.
Inside merobox's default 1-host setup this is fine — all inter-node libp2p
traffic flows over the bridge.

Reconnect typically needs a few seconds for libp2p mesh reformation
(heartbeats + peer discovery), so workflows should pair this with an
explicit `wait_for_sync` or short `wait` before asserting state propagation.
"""

from typing import Any

from merobox.commands.bootstrap.steps._docker_utils import (
    get_docker_client,
    is_binary_mode,
    warn_if_mdns_enabled,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console

DEFAULT_NETWORK = "bridge"


class DisconnectNodeStep(BaseStep):
    """Disconnect a node container from a Docker network (default: bridge)."""

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
        network_name = self._resolve_dynamic_value(
            self.config.get("network", DEFAULT_NETWORK),
            workflow_results,
            dynamic_values,
        )

        console.print(
            f"[yellow]Disconnecting {node_name} from network {network_name}...[/yellow]"
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except Exception as exc:
            console.print(f"[red]✗ Container '{node_name}' not found: {exc}[/red]")
            return False

        warn_if_mdns_enabled(container, node_name)

        try:
            network = client.networks.get(network_name)
            network.disconnect(container)
        except Exception as exc:
            console.print(
                f"[red]✗ Failed to disconnect {node_name} from {network_name}: {exc}[/red]"
            )
            return False

        console.print(f"[green]✓ Disconnected {node_name} from {network_name}[/green]")
        return True


class ConnectNodeStep(BaseStep):
    """Connect a node container back to a Docker network (default: bridge)."""

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
        network_name = self._resolve_dynamic_value(
            self.config.get("network", DEFAULT_NETWORK),
            workflow_results,
            dynamic_values,
        )

        console.print(
            f"[yellow]Connecting {node_name} to network {network_name}...[/yellow]"
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except Exception as exc:
            console.print(f"[red]✗ Container '{node_name}' not found: {exc}[/red]")
            return False

        try:
            network = client.networks.get(network_name)
            network.connect(container)
        except Exception as exc:
            console.print(
                f"[red]✗ Failed to connect {node_name} to {network_name}: {exc}[/red]"
            )
            return False

        console.print(f"[green]✓ Connected {node_name} to {network_name}[/green]")
        return True
