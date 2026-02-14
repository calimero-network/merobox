"""
AuthServiceManager - Auth service stack (Traefik + Auth) management.
"""

import os
import time
from typing import Optional

import docker
from rich.console import Console

from merobox.commands.managers.base import BaseManager
from merobox.commands.managers.network import NetworkManager

console = Console()


class AuthServiceManager(BaseManager):
    """Manages the auth service stack including Traefik proxy and auth service."""

    def __init__(self, client: Optional[docker.DockerClient] = None):
        """Initialize the AuthServiceManager.

        Args:
            client: Optional Docker client. If not provided, creates one from environment.
        """
        super().__init__(client)
        self.network_manager = NetworkManager(self.client)

    def start_auth_service_stack(
        self, auth_image: str = None, auth_use_cached: bool = False
    ) -> bool:
        """Start the Traefik proxy and auth service containers.

        Args:
            auth_image: Optional custom auth service image.
            auth_use_cached: Whether to use cached auth frontend.

        Returns:
            True if started successfully, False otherwise.
        """
        try:
            console.print(
                "[yellow]Starting auth service stack (Traefik + Auth)...[/yellow]"
            )

            # Check if auth service and traefik are already running
            auth_running = self._is_container_running("auth")
            traefik_running = self._is_container_running("proxy")

            if auth_running and traefik_running:
                console.print("[green]✓ Auth service stack is already running[/green]")
                return True

            # Ensure networks exist first
            self.network_manager.ensure_auth_networks()

            # Start Traefik proxy first
            if not traefik_running:
                if not self._start_traefik_container():
                    return False

            # Start Auth service
            if not auth_running:
                if not self._start_auth_container(auth_image, auth_use_cached):
                    return False

            # Wait a bit for services to be ready
            console.print("[yellow]Waiting for services to be ready...[/yellow]")
            time.sleep(5)

            # Verify services are running
            if self._is_container_running("auth") and self._is_container_running(
                "proxy"
            ):
                console.print("[green]✓ Auth service stack is healthy[/green]")
                return True
            else:
                console.print(
                    "[yellow]⚠️  Auth service stack started but may not be fully ready[/yellow]"
                )
                return True

        except Exception as e:
            console.print(f"[red]✗ Error starting auth service stack: {str(e)}[/red]")
            return False

    def _start_traefik_container(self) -> bool:
        """Start the Traefik proxy container.

        Returns:
            True if started successfully, False otherwise.
        """
        try:
            console.print("[yellow]Starting Traefik proxy...[/yellow]")

            # Remove existing container if it exists
            try:
                existing = self.client.containers.get("proxy")
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Pull Traefik image
            if not self._ensure_image_pulled("traefik:v2.10"):
                return False

            # Create and start Traefik container
            traefik_config = {
                "name": "proxy",
                "image": "traefik:v2.10",
                "detach": True,
                "command": [
                    "--api.insecure=true",
                    "--providers.docker=true",
                    "--entrypoints.web.address=:80",
                    "--accesslog=true",
                    "--log.level=DEBUG",
                    "--providers.docker.exposedByDefault=false",
                    "--providers.docker.network=calimero_web",
                    "--serversTransport.forwardingTimeouts.dialTimeout=30s",
                    "--serversTransport.forwardingTimeouts.responseHeaderTimeout=30s",
                    "--serversTransport.forwardingTimeouts.idleConnTimeout=30s",
                ],
                "ports": {"80/tcp": 80, "8080/tcp": 8080},
                "volumes": {
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "ro",
                    }
                },
                "network": "calimero_web",
                "restart_policy": {"Name": "unless-stopped"},
                "labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.proxy-dashboard.rule": "Host(`proxy.127.0.0.1.nip.io`)",
                    "traefik.http.routers.proxy-dashboard.entrypoints": "web",
                    "traefik.http.routers.proxy-dashboard.service": "api@internal",
                },
            }

            self.client.containers.run(**traefik_config)
            console.print("[green]✓ Traefik proxy started[/green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start Traefik proxy: {str(e)}[/red]")
            return False

    def _start_auth_container(
        self, auth_image: str = None, auth_use_cached: bool = False
    ) -> bool:
        """Start the Auth service container.

        Args:
            auth_image: Optional custom auth service image.
            auth_use_cached: Whether to use cached auth frontend.

        Returns:
            True if started successfully, False otherwise.
        """
        try:
            console.print("[yellow]Starting Auth service...[/yellow]")

            # Remove existing container if it exists
            try:
                existing = self.client.containers.get("auth")
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Pull Auth service image
            auth_image_to_use = auth_image or "ghcr.io/calimero-network/mero-auth:edge"

            # Ensure auth image is available
            if not self._ensure_image_pulled(auth_image_to_use):
                console.print(
                    "[yellow]⚠️  Warning: Could not pull auth image, trying with local image[/yellow]"
                )

            # Create volume for auth data if it doesn't exist
            try:
                self.client.volumes.get("calimero_auth_data")
            except docker.errors.NotFound:
                self.client.volumes.create("calimero_auth_data")

            # Prepare environment variables for auth service
            auth_env = ["RUST_LOG=debug"]

            # By default, fetch fresh auth frontend unless explicitly disabled
            env_auth_fetch = os.getenv("CALIMERO_AUTH_FRONTEND_FETCH", "1")
            should_use_cached = auth_use_cached or env_auth_fetch == "0"

            if not should_use_cached:
                auth_env.append("CALIMERO_AUTH_FRONTEND_FETCH=1")
                if env_auth_fetch == "1" and not auth_use_cached:
                    console.print(
                        "[cyan]Using default fresh auth frontend fetch for auth service[/cyan]"
                    )
                else:
                    console.print(
                        "[cyan]Setting CALIMERO_AUTH_FRONTEND_FETCH=1 for auth service[/cyan]"
                    )
            else:
                if auth_use_cached:
                    console.print(
                        "[cyan]Using cached auth frontend (--auth-use-cached flag)[/cyan]"
                    )
                else:
                    console.print(
                        "[cyan]Environment variable CALIMERO_AUTH_FRONTEND_FETCH=0 detected, using cached auth frontend[/cyan]"
                    )

            auth_config = {
                "name": "auth",
                "image": auth_image_to_use,
                "detach": True,
                "user": "root",
                "volumes": {"calimero_auth_data": {"bind": "/data", "mode": "rw"}},
                "environment": auth_env,
                "network": "calimero_web",
                "restart_policy": {"Name": "unless-stopped"},
                "labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.auth-public.rule": "Host(`localhost`) && (PathPrefix(`/auth/`) || PathPrefix(`/admin/`))",
                    "traefik.http.routers.auth-public.entrypoints": "web",
                    "traefik.http.routers.auth-public.service": "auth-service",
                    "traefik.http.routers.auth-public.middlewares": "cors,auth-headers",
                    "traefik.http.routers.auth-public.priority": "100",
                    "traefik.http.middlewares.auth-headers.headers.customrequestheaders.X-Node-ID": "auth",
                    "traefik.http.services.auth-service.loadbalancer.server.port": "3001",
                    "traefik.http.middlewares.cors.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
                    "traefik.http.middlewares.cors.headers.accesscontrolallowheaders": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolmaxage": "100",
                    "traefik.http.middlewares.cors.headers.addvaryheader": "true",
                    "traefik.http.middlewares.cors.headers.accesscontrolexposeheaders": "X-Auth-Error",
                },
            }

            container = self.client.containers.run(**auth_config)

            # Connect to the internal network as well
            try:
                internal_network = self.client.networks.get("calimero_internal")
                internal_network.connect(container)
                console.print(
                    "[cyan]✓ Auth service connected to internal network[/cyan]"
                )
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not connect auth to internal network: {str(e)}[/yellow]"
                )
            console.print("[green]✓ Auth service started[/green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start Auth service: {str(e)}[/red]")
            return False

    def stop_auth_service_stack(self) -> bool:
        """Stop the Traefik proxy and auth service containers.

        Returns:
            True if stopped successfully, False otherwise.
        """
        try:
            console.print("[yellow]Stopping auth service stack...[/yellow]")

            success = True
            # Stop auth service
            try:
                auth_container = self.client.containers.get("auth")
                auth_container.stop()
                auth_container.remove()
                console.print("[green]✓ Auth service stopped[/green]")
            except docker.errors.NotFound:
                console.print("[cyan]• Auth service was not running[/cyan]")
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not stop auth service: {str(e)}[/yellow]"
                )
                success = False

            # Stop Traefik proxy
            try:
                proxy_container = self.client.containers.get("proxy")
                proxy_container.stop()
                proxy_container.remove()
                console.print("[green]✓ Traefik proxy stopped[/green]")
            except docker.errors.NotFound:
                console.print("[cyan]• Traefik proxy was not running[/cyan]")
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not stop Traefik proxy: {str(e)}[/yellow]"
                )
                success = False

            if success:
                console.print(
                    "[green]✓ Auth service stack stopped successfully[/green]"
                )

            return success

        except Exception as e:
            console.print(f"[red]✗ Error stopping auth service stack: {str(e)}[/red]")
            return False

    def get_auth_labels_for_node(self, node_name: str) -> dict:
        """Generate Traefik labels for a node's auth service integration.

        Args:
            node_name: Name of the node to generate labels for.

        Returns:
            Dictionary of Traefik labels for the node.
        """
        return {
            "traefik.enable": "true",
            f"traefik.http.routers.{node_name}-api.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && (PathPrefix(`/jsonrpc`) || PathPrefix(`/admin-api/`))",
            f"traefik.http.routers.{node_name}-api.entrypoints": "web",
            f"traefik.http.routers.{node_name}-api.service": f"{node_name}-core",
            f"traefik.http.routers.{node_name}-api.middlewares": f"cors,auth-{node_name}",
            f"traefik.http.routers.{node_name}-ws.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/ws`)",
            f"traefik.http.routers.{node_name}-ws.entrypoints": "web",
            f"traefik.http.routers.{node_name}-ws.service": f"{node_name}-core",
            f"traefik.http.routers.{node_name}-ws.middlewares": f"cors,auth-{node_name}",
            f"traefik.http.routers.{node_name}-sse.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/sse`)",
            f"traefik.http.routers.{node_name}-sse.entrypoints": "web",
            f"traefik.http.routers.{node_name}-sse.service": f"{node_name}-core",
            f"traefik.http.routers.{node_name}-sse.middlewares": f"cors-sse-{node_name},auth-{node_name}",
            f"traefik.http.routers.{node_name}-dashboard.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/admin-dashboard`)",
            f"traefik.http.routers.{node_name}-dashboard.entrypoints": "web",
            f"traefik.http.routers.{node_name}-dashboard.service": f"{node_name}-core",
            f"traefik.http.routers.{node_name}-dashboard.middlewares": "cors",
            f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && (PathPrefix(`/auth/`) || PathPrefix(`/admin/`))",
            f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.entrypoints": "web",
            f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.service": "auth-service",
            f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.middlewares": "cors,auth-headers",
            f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.priority": "200",
            f"traefik.http.middlewares.auth-{node_name}.forwardauth.address": "http://auth:3001/auth/validate",
            f"traefik.http.middlewares.auth-{node_name}.forwardauth.trustForwardHeader": "true",
            f"traefik.http.middlewares.auth-{node_name}.forwardauth.authResponseHeaders": "X-Auth-User,X-Auth-Permissions",
            f"traefik.http.services.{node_name}-core.loadbalancer.server.port": "2528",
            "traefik.http.middlewares.cors.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
            "traefik.http.middlewares.cors.headers.accesscontrolallowheaders": "*",
            "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist": "*",
            "traefik.http.middlewares.cors.headers.accesscontrolmaxage": "100",
            "traefik.http.middlewares.cors.headers.addvaryheader": "true",
            "traefik.http.middlewares.cors.headers.accesscontrolexposeheaders": "X-Auth-Error",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowmethods": "GET,OPTIONS",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowheaders": "Cache-Control,Last-Event-ID,Accept,Accept-Language,Content-Language,Content-Type,Authorization",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolalloworiginlist": "*",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolmaxage": "86400",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.addvaryheader": "true",
            f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolexposeheaders": "X-Auth-Error",
        }
