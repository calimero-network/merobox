"""
BaseManager - Common Docker client utilities and shared functionality.
"""

import sys
from typing import Optional

import docker
from rich.console import Console

console = Console()


class BaseManager:
    """Base class with shared Docker client utilities."""

    def __init__(self, client: Optional[docker.DockerClient] = None):
        """Initialize with an optional Docker client.

        Args:
            client: Optional Docker client. If not provided, creates one from environment.
        """
        if client is not None:
            self.client = client
        else:
            try:
                self.client = docker.from_env()
            except Exception as e:
                console.print(f"[red]Failed to connect to Docker: {str(e)}[/red]")
                console.print(
                    "[yellow]Make sure Docker is running and you have permission to access it.[/yellow]"
                )
                sys.exit(1)

    def _is_remote_image(self, image: str) -> bool:
        """Check if the image name indicates a remote registry."""
        return "/" in image and ":" in image

    def force_pull_image(self, image: str) -> bool:
        """Force pull an image even if it exists locally."""
        try:
            console.print(f"[yellow]Force pulling image: {image}[/yellow]")

            # Remove local image if it exists
            try:
                self.client.images.get(image)
                console.print(f"[cyan]Removing local image: {image}[/cyan]")
                self.client.images.remove(image, force=True)
            except docker.errors.ImageNotFound:
                pass

            # Pull the fresh image
            return self._ensure_image_pulled(image)

        except Exception as e:
            console.print(f"[red]✗ Error force pulling image {image}: {str(e)}[/red]")
            return False

    def _ensure_image_pulled(self, image: str) -> bool:
        """Ensure the specified Docker image is available locally, pulling if remote."""
        try:
            # Check if image exists locally
            try:
                self.client.images.get(image)
                console.print(f"[cyan]✓ Image {image} already available locally[/cyan]")
                return True
            except docker.errors.ImageNotFound:
                pass

            # Image not found locally, attempt to pull it
            console.print(f"[yellow]Pulling image: {image}[/yellow]")
            try:
                self.client.images.pull(image)
                console.print(f"[green]✓ Successfully pulled image: {image}[/green]")
                return True

            except docker.errors.NotFound:
                console.print(f"[red]✗ Image {image} not found in registry[/red]")
                return False
            except docker.errors.APIError as e:
                console.print(
                    f"[red]✗ Docker API error pulling {image}: {str(e)}[/red]"
                )
                return False
            except Exception as e:
                console.print(f"[red]✗ Failed to pull image {image}: {str(e)}[/red]")
                return False

        except Exception as e:
            console.print(
                f"[red]✗ Error checking/pulling image {image}: {str(e)}[/red]"
            )
            return False

    def _extract_host_port(self, container, container_port: str) -> Optional[int]:
        """Extract the published host port for a given container port."""
        try:
            ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            host_bindings = ports.get(container_port)
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort")
                    if host_port and host_port.isdigit():
                        return int(host_port)

            port_bindings = (
                container.attrs.get("HostConfig", {}).get("PortBindings") or {}
            )
            host_bindings = port_bindings.get(container_port)
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort")
                    if host_port and host_port.isdigit():
                        return int(host_port)

            env_vars = container.attrs.get("Config", {}).get("Env") or []
            for env_entry in env_vars:
                if isinstance(env_entry, str) and env_entry.startswith(
                    "HOST_RPC_PORT="
                ):
                    value = env_entry.split("=", 1)[1]
                    if value.isdigit():
                        return int(value)
        except Exception:
            return None

        return None

    def _is_container_running(self, container_name: str) -> bool:
        """Check if a container is running."""
        try:
            container = self.client.containers.get(container_name)
            return container.status == "running"
        except docker.errors.NotFound:
            return False
        except Exception:
            return False
