"""
NetworkManager - Docker network management for Calimero services.
"""

from typing import Optional

import docker
from rich.console import Console

from merobox.commands.managers.base import BaseManager

console = Console()


class NetworkManager(BaseManager):
    """Manages Docker networks for Calimero auth service integration."""

    def __init__(self, client: Optional[docker.DockerClient] = None):
        """Initialize the NetworkManager.

        Args:
            client: Optional Docker client. If not provided, creates one from environment.
        """
        super().__init__(client)

    def ensure_auth_networks(self) -> bool:
        """Ensure the auth service networks exist for Traefik integration.

        Returns:
            True if networks are ready, False on error.
        """
        try:
            networks_to_create = [
                {"name": "calimero_web", "driver": "bridge"},
                {"name": "calimero_internal", "driver": "bridge", "internal": True},
            ]

            for network_spec in networks_to_create:
                network_name = network_spec["name"]
                try:
                    # Check if network already exists
                    self.client.networks.get(network_name)
                    console.print(
                        f"[cyan]✓ Network {network_name} already exists[/cyan]"
                    )
                except docker.errors.NotFound:
                    # Create the network
                    console.print(f"[yellow]Creating network: {network_name}[/yellow]")
                    network_config = {
                        "name": network_name,
                        "driver": network_spec["driver"],
                    }
                    if network_spec.get("internal"):
                        network_config["internal"] = True

                    self.client.networks.create(**network_config)
                    console.print(f"[green]✓ Created network: {network_name}[/green]")

            return True

        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Could not ensure auth networks: {str(e)}[/yellow]"
            )
            return False

    def get_network(self, network_name: str):
        """Get a Docker network by name.

        Args:
            network_name: Name of the network to get.

        Returns:
            The Docker network object, or None if not found.
        """
        try:
            return self.client.networks.get(network_name)
        except docker.errors.NotFound:
            return None
        except Exception:
            return None

    def connect_container_to_network(self, container, network_name: str) -> bool:
        """Connect a container to a network.

        Args:
            container: The Docker container to connect.
            network_name: Name of the network to connect to.

        Returns:
            True if connected successfully, False otherwise.
        """
        try:
            network = self.client.networks.get(network_name)
            network.connect(container)
            return True
        except docker.errors.NotFound:
            console.print(f"[yellow]Network {network_name} not found[/yellow]")
            return False
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Could not connect to {network_name}: {str(e)}[/yellow]"
            )
            return False
