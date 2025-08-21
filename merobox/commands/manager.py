"""
Calimero Manager - Core functionality for managing Calimero nodes in Docker containers.
"""

import docker
import time
import os
import sys
from rich.console import Console
from rich.table import Table
from typing import Dict, List, Optional, Any

console = Console()


class CalimeroManager:
    """Manages Calimero nodes in Docker containers."""

    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception as e:
            console.print(f"[red]Failed to connect to Docker: {str(e)}[/red]")
            console.print(
                "[yellow]Make sure Docker is running and you have permission to access it.[/yellow]"
            )
            sys.exit(1)
        self.nodes = {}

    def _is_remote_image(self, image: str) -> bool:
        """Check if the image name indicates a remote registry."""
        # Check if image contains a registry (has slashes and a tag)
        return "/" in image and ":" in image

    def force_pull_image(self, image: str) -> bool:
        """Force pull an image even if it exists locally."""
        try:
            console.print(f"[yellow]Force pulling image: {image}[/yellow]")

            # Remove local image if it exists
            try:
                local_image = self.client.images.get(image)
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
                # Pull the image
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

    def run_node(
        self,
        node_name: str,
        port: int = 2428,
        rpc_port: int = 2528,
        chain_id: str = "testnet-1",
        data_dir: str = None,
        image: str = None,
    ) -> bool:
        """Run a Calimero node container."""
        try:
            # Determine the image to use
            image_to_use = image or "ghcr.io/calimero-network/merod:edge"

            # Ensure the image is available
            if not self._ensure_image_pulled(image_to_use):
                console.print(
                    f"[red]✗ Cannot proceed without image: {image_to_use}[/red]"
                )
                return False

            # Check if containers already exist and clean them up
            for container_name in [node_name, f"{node_name}-init"]:
                try:
                    existing_container = self.client.containers.get(container_name)
                    if existing_container.status == "running":
                        console.print(
                            f"[yellow]Container {container_name} is already running, stopping it...[/yellow]"
                        )
                        try:
                            existing_container.stop()
                            existing_container.remove()
                            console.print(
                                f"[green]✓ Cleaned up existing container {container_name}[/green]"
                            )
                        except Exception as stop_error:
                            console.print(
                                f"[yellow]⚠️  Could not stop container {container_name}: {str(stop_error)}[/yellow]"
                            )
                            console.print(f"[yellow]Trying to force remove...[/yellow]")
                            try:
                                # Try to force remove the container
                                existing_container.remove(force=True)
                                console.print(
                                    f"[green]✓ Force removed container {container_name}[/green]"
                                )
                            except Exception as force_error:
                                console.print(
                                    f"[red]✗ Could not remove container {container_name}: {str(force_error)}[/red]"
                                )
                                console.print(
                                    f"[yellow]Container may need manual cleanup. Continuing with deployment...[/yellow]"
                                )
                                # Continue anyway - the new container will have a different name
                    else:
                        # Container exists but not running, just remove it
                        existing_container.remove()
                        console.print(
                            f"[green]✓ Cleaned up existing container {container_name}[/green]"
                        )
                except docker.errors.NotFound:
                    pass

            # Set container names (using standard names since we've cleaned up)
            container_name = node_name
            init_container_name = f"{node_name}-init"

            # Prepare data directory
            if data_dir is None:
                data_dir = f"./data/{node_name}"

            # Create data directory if it doesn't exist
            os.makedirs(data_dir, exist_ok=True)

            # Create the node-specific subdirectory that merod expects
            node_data_dir = os.path.join(data_dir, node_name)
            os.makedirs(node_data_dir, exist_ok=True)

            # Set permissions to be world-writable since container runs as root
            os.chmod(data_dir, 0o777)
            os.chmod(node_data_dir, 0o777)

            # Prepare container configuration
            container_config = {
                "name": container_name,
                "image": image_to_use,
                "detach": True,
                "user": "root",  # Override the default user in the image
                "privileged": True,  # Run in privileged mode to avoid permission issues
                "environment": {
                    "CALIMERO_HOME": "/app/data",
                    "NODE_NAME": node_name,
                    "RUST_LOG": "info",
                },
                "ports": {
                    "2428/tcp": port,  # Map external P2P port to internal P2P port (0.0.0.0:2428)
                    "2528/tcp": rpc_port,  # Map external RPC port to internal admin server port (127.0.0.1:2528)
                },
                "volumes": {
                    os.path.abspath(data_dir): {"bind": "/app/data", "mode": "rw"}
                },
                "labels": {
                    "calimero.node": "true",
                    "node.name": node_name,
                    "chain.id": chain_id,
                },
            }

            # First, initialize the node
            console.print(f"[yellow]Initializing node {node_name}...[/yellow]")

            # Create a temporary container for initialization
            init_config = container_config.copy()
            init_config["name"] = init_container_name
            init_config["entrypoint"] = ""
            init_config["command"] = [
                "merod",
                "--home",
                "/app/data",
                "--node-name",
                node_name,
                "init",
                "--server-host",
                "0.0.0.0",
                "--server-port",
                str(2528),
                "--swarm-port",
                str(2428),
            ]
            init_config["detach"] = False

            try:
                init_container = self.client.containers.run(**init_config)
                console.print(
                    f"[green]✓ Node {node_name} initialized successfully[/green]"
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to initialize node {node_name}: {str(e)}[/red]"
                )
                return False
            finally:
                # Clean up init container
                try:
                    init_container.remove()
                except:
                    pass

            # Now start the actual node
            console.print(f"[yellow]Starting node {node_name}...[/yellow]")
            run_config = container_config.copy()
            run_config["entrypoint"] = ""
            run_config["command"] = [
                "merod",
                "--home",
                "/app/data",
                "--node-name",
                node_name,
                "run",
            ]

            container = self.client.containers.run(**run_config)
            self.nodes[node_name] = container

            # Wait a moment and check if container is still running
            time.sleep(3)
            container.reload()

            if container.status != "running":
                # Container failed to start, get logs
                logs = container.logs().decode("utf-8")
                container.remove()
                console.print(f"[red]✗ Node {node_name} failed to start[/red]")
                console.print(f"[yellow]Container logs:[/yellow]")
                console.print(logs)

                # Check for common issues
                if "GLIBC" in logs:
                    console.print(f"\n[red]GLIBC Compatibility Issue Detected[/red]")
                    console.print(
                        f"[yellow]The Calimero binary requires newer GLIBC versions.[/yellow]"
                    )
                    console.print(f"[yellow]Try one of these solutions:[/yellow]")
                    console.print(f"  1. Use a different base image (--image option)")
                    console.print(f"  2. Build from source")
                    console.print(f"  3. Use a compatible Docker base image")

                return False

            console.print(
                f"[green]✓ Started Calimero node {node_name} (ID: {container.short_id})[/green]"
            )
            console.print(f"  - P2P Port: {port}")
            console.print(f"  - RPC/Admin Port: {rpc_port}")
            console.print(f"  - Chain ID: {chain_id}")
            console.print(f"  - Data Directory: {data_dir}")
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start node {node_name}: {str(e)}[/red]")
            return False

    def _find_available_ports(self, count: int, start_port: int = 2428) -> List[int]:
        """Find available ports starting from start_port."""
        import socket

        available_ports = []
        current_port = start_port

        while len(available_ports) < count:
            try:
                # Try to bind to the port to check if it's available
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("localhost", current_port))
                    available_ports.append(current_port)
            except OSError:
                # Port is in use, try next
                pass
            current_port += 1

            # Safety check to prevent infinite loop
            if current_port > start_port + 1000:
                raise RuntimeError(
                    f"Could not find {count} available ports starting from {start_port}"
                )

        return available_ports

    def run_multiple_nodes(
        self,
        count: int,
        base_port: int = None,
        base_rpc_port: int = None,
        chain_id: str = "testnet-1",
        prefix: str = "calimero-node",
        image: str = None,
    ) -> bool:
        """Run multiple Calimero nodes with automatic port allocation."""
        console.print(f"[bold]Starting {count} Calimero nodes...[/bold]")

        # Find available ports automatically if not specified
        if base_port is None:
            p2p_ports = self._find_available_ports(count, 2428)
        else:
            p2p_ports = [base_port + i for i in range(count)]

        if base_rpc_port is None:
            # Use a different range for RPC ports to avoid conflicts
            rpc_ports = self._find_available_ports(count, 2528)
        else:
            rpc_ports = [base_rpc_port + i for i in range(count)]

        success_count = 0
        for i in range(count):
            node_name = f"{prefix}-{i+1}"
            port = p2p_ports[i]
            rpc_port = rpc_ports[i]

            if self.run_node(node_name, port, rpc_port, chain_id, image=image):
                success_count += 1
            else:
                console.print(
                    f"[red]Failed to start node {node_name}, stopping deployment[/red]"
                )
                break

        console.print(
            f"\n[bold]Deployment Summary: {success_count}/{count} nodes started successfully[/bold]"
        )
        return success_count == count

    def stop_node(self, node_name: str) -> bool:
        """Stop a Calimero node container."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
                container.stop(timeout=10)
                container.remove()
                del self.nodes[node_name]
                console.print(f"[green]✓ Stopped and removed node {node_name}[/green]")
                return True
            else:
                # Try to find container by name
                try:
                    container = self.client.containers.get(node_name)
                    container.stop(timeout=10)
                    container.remove()
                    console.print(
                        f"[green]✓ Stopped and removed node {node_name}[/green]"
                    )
                    return True
                except docker.errors.NotFound:
                    console.print(f"[yellow]Node {node_name} not found[/yellow]")
                    return False
        except Exception as e:
            console.print(f"[red]✗ Failed to stop node {node_name}: {str(e)}[/red]")
            return False

    def stop_all_nodes(self) -> bool:
        """Stop all running Calimero nodes."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
                return True

            console.print(f"[bold]Stopping {len(containers)} Calimero nodes...[/bold]")

            success_count = 0
            failed_nodes = []

            for container in containers:
                try:
                    container.stop(timeout=10)
                    container.remove()
                    console.print(
                        f"[green]✓ Stopped and removed {container.name}[/green]"
                    )
                    success_count += 1

                    # Remove from nodes dict if present
                    if container.name in self.nodes:
                        del self.nodes[container.name]

                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to stop {container.name}: {str(e)}[/red]"
                    )
                    failed_nodes.append(container.name)

            console.print(
                f"\n[bold]Stop Summary: {success_count}/{len(containers)} nodes stopped successfully[/bold]"
            )

            if failed_nodes:
                console.print(f"[red]Failed to stop: {', '.join(failed_nodes)}[/red]")
                return False

            return True

        except Exception as e:
            console.print(f"[red]Failed to stop all nodes: {str(e)}[/red]")
            return False

    def get_running_nodes(self) -> List[str]:
        """Return a list of names for running Calimero node containers."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true", "status": "running"}
            )
            return [c.name for c in containers]
        except Exception:
            return []

    def list_nodes(self) -> None:
        """List all running Calimero nodes."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
                return

            table = Table(title="Running Calimero Nodes")
            table.add_column("Name", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Image", style="blue")
            table.add_column("P2P Port", style="yellow")
            table.add_column("RPC/Admin Port", style="yellow")
            table.add_column("Chain ID", style="magenta")
            table.add_column("Created", style="white")

            for container in containers:
                # Extract ports from container attributes
                p2p_port = "N/A"
                rpc_port = "N/A"

                # Get port mappings from container attributes
                if container.attrs.get("NetworkSettings", {}).get("Ports"):
                    port_mappings = container.attrs["NetworkSettings"]["Ports"]
                    port_list = []

                    for container_port, host_bindings in port_mappings.items():
                        if host_bindings:
                            for binding in host_bindings:
                                if "HostPort" in binding:
                                    port_list.append(int(binding["HostPort"]))

                    # Remove duplicates and sort ports
                    port_list = sorted(list(set(port_list)))

                    # Assign P2P and RPC ports
                    if len(port_list) >= 2:
                        p2p_port = str(port_list[0])
                        rpc_port = str(port_list[1])
                    elif len(port_list) == 1:
                        p2p_port = str(port_list[0])

                # Extract chain ID from labels
                chain_id = container.labels.get("chain.id", "N/A")

                table.add_row(
                    container.name,
                    container.status,
                    (
                        container.image.tags[0]
                        if container.image.tags
                        else container.image.id[:12]
                    ),
                    p2p_port,
                    rpc_port,
                    chain_id,
                    container.attrs["Created"][:19].replace("T", " "),
                )

            console.print(table)

        except Exception as e:
            console.print(f"[red]Failed to list nodes: {str(e)}[/red]")

    def get_node_logs(self, node_name: str, tail: int = 100) -> None:
        """Get logs from a specific node."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
            else:
                container = self.client.containers.get(node_name)

            logs = container.logs(tail=tail, timestamps=True).decode("utf-8")
            console.print(f"\n[bold]Logs for {node_name}:[/bold]")
            console.print(logs)

        except Exception as e:
            console.print(f"[red]Failed to get logs for {node_name}: {str(e)}[/red]")

    def verify_admin_binding(self, node_name: str) -> bool:
        """Verify that the admin server is properly bound to localhost."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
            else:
                container = self.client.containers.get(node_name)

            # Check if admin server is listening on localhost
            result = container.exec_run(
                f"sh -c 'timeout 3 bash -c \"</dev/tcp/127.0.0.1/2528\"' 2>&1 || echo 'Connection failed'"
            )

            if "Connection failed" in result.output.decode():
                console.print(
                    f"[red]✗ Admin server not accessible on localhost:2528 for {node_name}[/red]"
                )
                return False
            else:
                console.print(
                    f"[green]✓ Admin server accessible on localhost:2528 for {node_name}[/green]"
                )
                return True

        except Exception as e:
            console.print(
                f"[red]Failed to verify admin binding for {node_name}: {str(e)}[/red]"
            )
            return False
