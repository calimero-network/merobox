"""
NodeManager - Calimero node container management.
"""

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

import docker
from rich.console import Console
from rich.table import Table

from merobox.commands.config_utils import (
    apply_bootstrap_nodes,
    apply_e2e_defaults,
    apply_near_devnet_config_to_file,
)
from merobox.commands.managers.auth_service import AuthServiceManager
from merobox.commands.managers.base import BaseManager
from merobox.commands.managers.network import NetworkManager

console = Console()


class NodeManager(BaseManager):
    """Manages Calimero node containers."""

    def __init__(self, client: Optional[docker.DockerClient] = None):
        """Initialize the NodeManager.

        Args:
            client: Optional Docker client. If not provided, creates one from environment.
        """
        super().__init__(client)
        self.nodes = {}
        self.node_rpc_ports: dict[str, int] = {}

        # Initialize sub-managers with shared client
        self.network_manager = NetworkManager(self.client)
        self.auth_service_manager = AuthServiceManager(self.client)

    def get_node_rpc_port(self, node_name: str) -> Optional[int]:
        """Return the published RPC port for the given node, if available."""
        if node_name in self.node_rpc_ports:
            return self.node_rpc_ports[node_name]

        try:
            container = self.client.containers.get(node_name)
            container.reload()
            host_port = self._extract_host_port(container, "2528/tcp")
            if host_port is not None:
                self.node_rpc_ports[node_name] = host_port
            return host_port
        except docker.errors.NotFound:
            return None
        except Exception:
            return None

    def run_node(
        self,
        node_name: str,
        port: int = 2428,
        rpc_port: int = 2528,
        chain_id: str = "testnet-1",
        data_dir: str = None,
        image: str = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        workflow_id: str = None,
        e2e_mode: bool = False,
        config_path: str = None,
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,
        use_image_entrypoint: bool = False,
    ) -> bool:
        """Run a Calimero node container."""
        try:
            # Determine the image to use
            image_to_use = image or "ghcr.io/calimero-network/merod:prerelease"

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
                            console.print("[yellow]Trying to force remove...[/yellow]")
                            try:
                                existing_container.remove(force=True)
                                console.print(
                                    f"[green]✓ Force removed container {container_name}[/green]"
                                )
                            except Exception as force_error:
                                console.print(
                                    f"[red]✗ Could not remove container {container_name}: {str(force_error)}[/red]"
                                )
                                console.print(
                                    "[yellow]Container may need manual cleanup. Continuing with deployment...[/yellow]"
                                )
                    else:
                        existing_container.remove()
                        console.print(
                            f"[green]✓ Cleaned up existing container {container_name}[/green]"
                        )
                except docker.errors.NotFound:
                    pass

            # Set container names
            container_name = node_name
            init_container_name = f"{node_name}-init"

            # Prepare data directory
            if data_dir is None:
                data_dir = f"./data/{node_name}"

            os.makedirs(data_dir, exist_ok=True)

            node_data_dir = os.path.join(data_dir, node_name)
            os.makedirs(node_data_dir, exist_ok=True)

            # Set restrictive permissions (owner only) for sensitive node data
            os.chmod(data_dir, 0o700)
            os.chmod(node_data_dir, 0o700)

            # Handle custom config if provided
            skip_init = False
            if config_path is not None:
                config_source = Path(config_path)
                if not config_source.exists():
                    console.print(
                        f"[red]✗ Custom config file not found: {config_path}[/red]"
                    )
                    return False

                config_dest = os.path.join(node_data_dir, "config.toml")
                try:
                    shutil.copy2(config_source, config_dest)
                    console.print(
                        f"[green]✓ Copied custom config from {config_path} to {config_dest}[/green]"
                    )
                    skip_init = True
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to copy custom config: {str(e)}[/red]"
                    )
                    return False

            # Prepare environment variables for node
            node_env = {
                "CALIMERO_HOME": "/app/data",
                "NODE_NAME": node_name,
                "RUST_LOG": log_level,
                "RUST_BACKTRACE": rust_backtrace,
            }

            console.print(
                f"[cyan]Setting RUST_LOG for node {node_name}: {log_level}[/cyan]"
            )
            console.print(
                f"[cyan]Setting RUST_BACKTRACE for node {node_name}: {rust_backtrace}[/cyan]"
            )

            console.print(f"[yellow]Environment variables for {node_name}:[/yellow]")
            for key, value in node_env.items():
                console.print(f"  {key}={value}")

            # By default, fetch fresh WebUI unless explicitly disabled
            env_webui_fetch = os.getenv("CALIMERO_WEBUI_FETCH", "1")
            should_use_cached = webui_use_cached or env_webui_fetch == "0"

            if not should_use_cached:
                node_env["CALIMERO_WEBUI_FETCH"] = "1"
                if env_webui_fetch == "1" and not webui_use_cached:
                    console.print(
                        f"[cyan]Using default fresh WebUI fetch for node {node_name}[/cyan]"
                    )
                else:
                    console.print(
                        f"[cyan]Setting CALIMERO_WEBUI_FETCH=1 for node {node_name}[/cyan]"
                    )
            else:
                if webui_use_cached:
                    console.print(
                        f"[cyan]Using cached WebUI frontend for node {node_name} (--webui-use-cached flag)[/cyan]"
                    )
                else:
                    console.print(
                        f"[cyan]Environment variable CALIMERO_WEBUI_FETCH=0 detected, using cached WebUI for node {node_name}[/cyan]"
                    )

            container_config = {
                "name": container_name,
                "image": image_to_use,
                "detach": True,
                "user": "root",
                # Use specific capabilities instead of privileged mode for security
                "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
                "environment": node_env,
                "ports": {
                    "2428/tcp": port,
                    "2528/tcp": rpc_port,
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

            # Near Devnet and E2E mode support
            if near_devnet_config or e2e_mode:
                if "extra_hosts" not in container_config:
                    container_config["extra_hosts"] = {}
                container_config["extra_hosts"]["host.docker.internal"] = "host-gateway"

            # Add auth service configuration if enabled
            if auth_service:
                console.print(
                    f"[cyan]Configuring {node_name} for auth service integration...[/cyan]"
                )

                if not self.auth_service_manager.start_auth_service_stack(
                    auth_image, auth_use_cached
                ):
                    console.print(
                        "[yellow]⚠️  Warning: Auth service stack failed to start, but continuing with node setup[/yellow]"
                    )

                auth_labels = self.auth_service_manager.get_auth_labels_for_node(
                    node_name
                )
                container_config["labels"].update(auth_labels)

                self.network_manager.ensure_auth_networks()

            # Initialize the node (unless using custom config)
            if not skip_init:
                console.print(f"[yellow]Initializing node {node_name}...[/yellow]")

                init_config = container_config.copy()
                init_config["name"] = init_container_name
                if use_image_entrypoint:
                    init_config["command"] = [
                        "merod",
                        "--home",
                        "/app/data",
                        "--node",
                        node_name,
                        "init",
                        "--server-host",
                        "0.0.0.0",
                        "--server-port",
                        str(2528),
                        "--swarm-port",
                        str(2428),
                    ]
                else:
                    init_config["entrypoint"] = ""
                    init_config["command"] = [
                        "merod",
                        "--home",
                        "/app/data",
                        "--node",
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
                    try:
                        init_container.remove()
                    except Exception:
                        pass
            else:
                console.print(
                    f"[cyan]Skipping initialization for {node_name} (using custom config)[/cyan]"
                )

            config_file = os.path.join(node_data_dir, "config.toml")

            try:
                if near_devnet_config:
                    self._fix_permissions(node_data_dir)

                    console.print(
                        "[green]✓ Applying Near Devnet config for the node [/green]"
                    )
                    actual_config_file = Path(node_data_dir) / "config.toml"

                    if not apply_near_devnet_config_to_file(
                        actual_config_file,
                        node_name,
                        near_devnet_config["rpc_url"],
                        near_devnet_config["contract_id"],
                        near_devnet_config["account_id"],
                        near_devnet_config["public_key"],
                        near_devnet_config["secret_key"],
                    ):
                        console.print("[red]✗ Failed to apply NEAR Devnet config[/red]")
                        return False

                if e2e_mode:
                    if not near_devnet_config:
                        self._fix_permissions(node_data_dir)
                    apply_e2e_defaults(config_file, node_name, workflow_id)

                if bootstrap_nodes:
                    apply_bootstrap_nodes(config_file, node_name, bootstrap_nodes)

            except Exception:
                if e2e_mode:
                    console.print(
                        f"[cyan]Applying e2e defaults to {node_name} for test isolation...[/cyan]"
                    )
                    apply_e2e_defaults(config_file, node_name, workflow_id)

            # Now start the actual node
            console.print(f"[yellow]Starting node {node_name}...[/yellow]")
            run_config = container_config.copy()
            if use_image_entrypoint:
                run_config["command"] = [
                    "merod",
                    "--home",
                    "/app/data",
                    "--node",
                    node_name,
                    "run",
                ]
            else:
                run_config["entrypoint"] = ""
                run_config["command"] = [
                    "merod",
                    "--home",
                    "/app/data",
                    "--node",
                    node_name,
                    "run",
                ]

            if auth_service:
                run_config["network"] = "calimero_web"

            container = self.client.containers.run(**run_config)
            self.nodes[node_name] = container

            if auth_service:
                try:
                    internal_network = self.client.networks.get("calimero_internal")
                    internal_network.connect(container)
                    console.print(
                        f"[cyan]✓ {node_name} connected to internal network (secure backend)[/cyan]"
                    )
                    console.print(
                        f"[cyan]✓ {node_name} connected to web network (Traefik routing)[/cyan]"
                    )
                except Exception as e:
                    console.print(
                        f"[yellow]⚠️  Warning: Could not connect {node_name} to auth networks: {str(e)}[/yellow]"
                    )

            time.sleep(3)
            container.reload()

            if container.status != "running":
                logs = container.logs().decode("utf-8")
                container.remove()
                console.print(f"[red]✗ Node {node_name} failed to start[/red]")
                console.print("[yellow]Container logs:[/yellow]")
                console.print(logs)

                if "GLIBC" in logs:
                    console.print("\n[red]GLIBC Compatibility Issue Detected[/red]")
                    console.print(
                        "[yellow]The Calimero binary requires newer GLIBC versions.[/yellow]"
                    )
                    console.print("[yellow]Try one of these solutions:[/yellow]")
                    console.print("  1. Use a different base image (--image option)")
                    console.print("  2. Build from source")
                    console.print("  3. Use a compatible Docker base image")

                return False

            console.print(
                f"[green]✓ Started Calimero node {node_name} (ID: {container.short_id})[/green]"
            )
            console.print(f"  - P2P Port: {port}")
            console.print(f"  - RPC/Admin Port: {rpc_port}")
            console.print(f"  - Chain ID: {chain_id}")
            console.print(f"  - Data Directory: {data_dir}")
            host_rpc_port = self._extract_host_port(container, "2528/tcp")
            if host_rpc_port is None and rpc_port is not None:
                try:
                    host_rpc_port = int(rpc_port)
                except (TypeError, ValueError):
                    host_rpc_port = None
            if host_rpc_port is not None:
                self.node_rpc_ports[node_name] = host_rpc_port

            display_rpc_port = host_rpc_port if host_rpc_port is not None else rpc_port
            console.print(
                f"  - Non Auth Node URL: [link]http://localhost:{display_rpc_port}[/link]"
            )

            if auth_service:
                hostname = node_name.replace("calimero-", "").replace("-", "")
                console.print(
                    f"  - Auth Node URL: [link]http://{hostname}.127.0.0.1.nip.io[/link]"
                )
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start node {node_name}: {str(e)}[/red]")
            return False

    def _find_available_ports(self, count: int, start_port: int = 2428) -> list[int]:
        """Find available ports starting from start_port."""
        import socket

        available_ports = []
        current_port = start_port

        while len(available_ports) < count:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("localhost", current_port))
                    available_ports.append(current_port)
            except OSError:
                pass
            current_port += 1

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
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        workflow_id: str = None,
        e2e_mode: bool = False,
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,
        use_image_entrypoint: bool = False,
    ) -> bool:
        """Run multiple Calimero nodes with automatic port allocation."""
        console.print(f"[bold]Starting {count} Calimero nodes...[/bold]")

        if workflow_id is None:
            workflow_id = str(uuid.uuid4())[:8]
            console.print(f"[cyan]Generated shared workflow_id: {workflow_id}[/cyan]")

        if base_port is None:
            p2p_ports = self._find_available_ports(count, 2428)
        else:
            p2p_ports = [base_port + i for i in range(count)]

        if base_rpc_port is None:
            rpc_ports = self._find_available_ports(count, 2528)
        else:
            rpc_ports = [base_rpc_port + i for i in range(count)]

        success_count = 0
        for i in range(count):
            node_name = f"{prefix}-{i+1}"
            port = p2p_ports[i]
            rpc_port = rpc_ports[i]

            node_specific_near_config = None
            if near_devnet_config:
                if node_name in near_devnet_config:
                    node_specific_near_config = near_devnet_config[node_name]

            if self.run_node(
                node_name,
                port,
                rpc_port,
                chain_id,
                image=image,
                auth_service=auth_service,
                auth_image=auth_image,
                auth_use_cached=auth_use_cached,
                webui_use_cached=webui_use_cached,
                log_level=log_level,
                rust_backtrace=rust_backtrace,
                workflow_id=workflow_id,
                e2e_mode=e2e_mode,
                near_devnet_config=node_specific_near_config,
                bootstrap_nodes=bootstrap_nodes,
                use_image_entrypoint=use_image_entrypoint,
            ):
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
                self.node_rpc_ports.pop(node_name, None)
                return True
            else:
                try:
                    container = self.client.containers.get(node_name)
                    container.stop(timeout=10)
                    container.remove()
                    console.print(
                        f"[green]✓ Stopped and removed node {node_name}[/green]"
                    )
                    self.node_rpc_ports.pop(node_name, None)
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

            success = True
            success_count = 0
            failed_nodes = []

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
            else:
                console.print(
                    f"[bold]Stopping {len(containers)} Calimero nodes...[/bold]"
                )

                for container in containers:
                    try:
                        container.stop(timeout=10)
                        container.remove()
                        console.print(
                            f"[green]✓ Stopped and removed {container.name}[/green]"
                        )
                        success_count += 1
                        self.node_rpc_ports.pop(container.name, None)

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
                    console.print(
                        f"[red]Failed to stop: {', '.join(failed_nodes)}[/red]"
                    )
                    success = False

            return success

        except Exception as e:
            console.print(f"[red]Failed to stop all nodes: {str(e)}[/red]")
            return False

    def get_running_nodes(self) -> list[str]:
        """Return a list of names for running Calimero node containers."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true", "status": "running"}
            )
            return [c.name for c in containers]
        except Exception:
            return []

    def list_nodes(self) -> None:
        """List all running Calimero nodes and infrastructure."""
        try:
            node_containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            auth_containers = []
            try:
                auth_container = self.client.containers.get("auth")
                auth_containers.append(auth_container)
            except docker.errors.NotFound:
                pass

            try:
                proxy_container = self.client.containers.get("proxy")
                auth_containers.append(proxy_container)
            except docker.errors.NotFound:
                pass

            if not node_containers and not auth_containers:
                console.print(
                    "[yellow]No Calimero nodes or services are currently running[/yellow]"
                )
                return

            if node_containers:
                table = Table(title="Running Calimero Nodes")
                table.add_column("Name", style="cyan")
                table.add_column("Status", style="green")
                table.add_column("Image", style="blue")
                table.add_column("P2P Port", style="yellow")
                table.add_column("RPC/Admin Port", style="yellow")
                table.add_column("Chain ID", style="magenta")
                table.add_column("Created", style="white")

                for container in node_containers:
                    p2p_port = "N/A"
                    rpc_port = "N/A"

                    if container.attrs.get("NetworkSettings", {}).get("Ports"):
                        port_mappings = container.attrs["NetworkSettings"]["Ports"]
                        port_list = []

                        for _container_port, host_bindings in port_mappings.items():
                            if host_bindings:
                                for binding in host_bindings:
                                    if "HostPort" in binding:
                                        port_list.append(int(binding["HostPort"]))

                        port_list = sorted(set(port_list))

                        if len(port_list) >= 2:
                            p2p_port = str(port_list[0])
                            rpc_port = str(port_list[1])
                        elif len(port_list) == 1:
                            p2p_port = str(port_list[0])

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

            if auth_containers:
                auth_table = Table(title="Running Auth Infrastructure")
                auth_table.add_column("Service", style="cyan")
                auth_table.add_column("Status", style="green")
                auth_table.add_column("Image", style="blue")
                auth_table.add_column("Ports", style="yellow")
                auth_table.add_column("Networks", style="magenta")
                auth_table.add_column("Created", style="white")

                for container in auth_containers:
                    ports = []
                    if container.attrs.get("NetworkSettings", {}).get("Ports"):
                        port_mappings = container.attrs["NetworkSettings"]["Ports"]
                        for container_port, host_bindings in port_mappings.items():
                            if host_bindings:
                                for binding in host_bindings:
                                    if "HostPort" in binding:
                                        ports.append(
                                            f"{binding['HostPort']}:{container_port}"
                                        )
                            else:
                                ports.append(container_port)

                    ports_str = ", ".join(ports) if ports else "N/A"

                    networks = []
                    if container.attrs.get("NetworkSettings", {}).get("Networks"):
                        networks = list(
                            container.attrs["NetworkSettings"]["Networks"].keys()
                        )

                    networks_str = ", ".join(networks) if networks else "N/A"

                    service_type = (
                        "Auth Service" if container.name == "auth" else "Traefik Proxy"
                    )

                    auth_table.add_row(
                        service_type,
                        container.status,
                        (
                            container.image.tags[0]
                            if container.image.tags
                            else container.image.id[:12]
                        ),
                        ports_str,
                        networks_str,
                        container.attrs["Created"][:19].replace("T", " "),
                    )

                if node_containers:
                    console.print()
                console.print(auth_table)

            try:
                auth_volume = self.client.volumes.get("calimero_auth_data")
                console.print(
                    f"\n[cyan]Auth Data Volume:[/cyan] calimero_auth_data (created: {auth_volume.attrs.get('CreatedAt', 'N/A')[:19]})"
                )
            except docker.errors.NotFound:
                pass

        except Exception as e:
            console.print(f"[red]Failed to list infrastructure: {str(e)}[/red]")

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

            result = container.exec_run(
                "sh -c 'timeout 3 bash -c \"</dev/tcp/127.0.0.1/2528\"' 2>&1 || echo 'Connection failed'"
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

    def _fix_permissions(self, path: str):
        """Fix ownership and write permissions of files created by Docker."""
        if not hasattr(os, "getuid"):
            return

        try:
            uid = os.getuid()
            gid = os.getgid()

            self.client.containers.run(
                "alpine:latest",
                command=f"sh -c 'chown -R {uid}:{gid} /data && chmod -R u+w /data'",
                volumes={os.path.abspath(path): {"bind": "/data", "mode": "rw"}},
                remove=True,
            )
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Failed to fix permissions for {path}: {e}[/yellow]"
            )
