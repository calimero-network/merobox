"""
MockRelayerManager - Mock relayer container management.
"""

from typing import Optional

import docker
from rich.console import Console

from merobox.commands.managers.base import BaseManager

console = Console()

MOCK_RELAYER_IMAGE = "ghcr.io/calimero-network/mero-relayer:8ee178e"
MOCK_RELAYER_PORT = 63529
MOCK_RELAYER_NAME = "mock-relayer"


class MockRelayerManager(BaseManager):
    """Manages the mock relayer container for testing."""

    def __init__(self, client: Optional[docker.DockerClient] = None):
        """Initialize the MockRelayerManager.

        Args:
            client: Optional Docker client. If not provided, creates one from environment.
        """
        super().__init__(client)
        self.mock_relayer_url: Optional[str] = None

    def ensure_mock_relayer(self) -> Optional[str]:
        """Ensure a mock relayer container is running and return its host URL.

        Returns:
            The mock relayer URL if running, None if failed to start.
        """
        # Validate cached URL by checking if container is still running
        if self.mock_relayer_url:
            try:
                existing = self.client.containers.get(MOCK_RELAYER_NAME)
                existing.reload()
                if existing.status == "running":
                    return self.mock_relayer_url
                # Container stopped - clear cached URL and continue to restart
                console.print(
                    "[yellow]Mock relayer container stopped, restarting...[/yellow]"
                )
                self.mock_relayer_url = None
            except docker.errors.NotFound:
                # Container removed - clear cached URL and continue to restart
                console.print(
                    "[yellow]Mock relayer container not found, starting new one...[/yellow]"
                )
                self.mock_relayer_url = None
            except Exception as e:
                # Unexpected error - clear cached URL and continue
                console.print(
                    f"[yellow]Error checking mock relayer status: {e}, will attempt restart...[/yellow]"
                )
                self.mock_relayer_url = None

        try:
            existing = self.client.containers.get(MOCK_RELAYER_NAME)
            existing.reload()
            if existing.status == "running":
                host_port = self._extract_host_port(
                    existing, f"{MOCK_RELAYER_PORT}/tcp"
                )
                if host_port is None:
                    console.print(
                        "[red]✗ Mock relayer is running but could not determine host port[/red]"
                    )
                    return None
                self.mock_relayer_url = f"http://host.docker.internal:{host_port}"
                console.print(
                    f"[cyan]✓ Mock relayer already running at {self.mock_relayer_url}[/cyan]"
                )
                return self.mock_relayer_url

            console.print(
                f"[yellow]Found stopped mock relayer container '{MOCK_RELAYER_NAME}', removing...[/yellow]"
            )
            try:
                existing.remove(force=True)
            except Exception as remove_err:
                console.print(
                    f"[red]✗ Failed to clean up existing mock relayer: {remove_err}[/red]"
                )
                return None
        except docker.errors.NotFound:
            pass
        except Exception as e:
            console.print(f"[red]✗ Error inspecting mock relayer: {e}[/red]")
            return None

        # Pull image if needed
        if not self._ensure_image_pulled(MOCK_RELAYER_IMAGE):
            return None

        # Try preferred host port first, fall back to random if it's taken
        port_binding: Optional[int] = MOCK_RELAYER_PORT
        for attempt in range(2):
            try:
                container = self.client.containers.run(
                    name=MOCK_RELAYER_NAME,
                    image=MOCK_RELAYER_IMAGE,
                    detach=True,
                    ports={f"{MOCK_RELAYER_PORT}/tcp": port_binding},
                    command=["--enable-mock-relayer"],
                    environment={
                        "ENABLE_NEAR": "false",
                        "ENABLE_STARKNET": "false",
                        "ENABLE_ICP": "false",
                        "ENABLE_ETHEREUM": "false",
                    },
                    labels={"calimero.mock_relayer": "true"},
                )
                container.reload()
                host_port = self._extract_host_port(
                    container, f"{MOCK_RELAYER_PORT}/tcp"
                )
                if host_port is None:
                    if port_binding is not None:
                        # Fallback to requested port only if we explicitly requested it
                        host_port = port_binding
                    else:
                        # Random port was requested but we couldn't determine it
                        console.print(
                            "[red]✗ Failed to determine mock relayer host port[/red]"
                        )
                        container.remove(force=True)
                        return None
                self.mock_relayer_url = f"http://host.docker.internal:{host_port}"
                console.print(
                    f"[green]✓ Mock relayer started ({container.short_id}) at {self.mock_relayer_url}[/green]"
                )
                return self.mock_relayer_url
            except docker.errors.APIError as e:
                if attempt == 0 and "port is already allocated" in str(e).lower():
                    console.print(
                        f"[yellow]Port {MOCK_RELAYER_PORT} is in use, starting mock relayer on a random host port...[/yellow]"
                    )
                    port_binding = None
                    continue
                console.print(
                    f"[red]✗ Failed to start mock relayer container: {str(e)}[/red]"
                )
                return None
            except Exception as e:
                console.print(
                    f"[red]✗ Unexpected error starting mock relayer: {str(e)}[/red]"
                )
                return None

        # Loop exhausted without success (should not normally reach here)
        return None

    def stop_mock_relayer(self) -> bool:
        """Stop the mock relayer container.

        Returns:
            True if stopped successfully, False otherwise.
        """
        try:
            relayer = self.client.containers.get(MOCK_RELAYER_NAME)
            if relayer.status == "running":
                console.print("[cyan]Stopping mock relayer container...[/cyan]")
                relayer.stop(timeout=10)
            relayer.remove()
            console.print("[green]✓ Mock relayer stopped[/green]")
            self.mock_relayer_url = None
            return True
        except docker.errors.NotFound:
            return True
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Failed to stop mock relayer: {e}[/yellow]"
            )
            return False

    def get_mock_relayer_url(self) -> Optional[str]:
        """Get the cached mock relayer URL.

        Returns:
            The mock relayer URL if available, None otherwise.
        """
        return self.mock_relayer_url
