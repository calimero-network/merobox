"""Restart-container step executor.

Wraps `docker restart` for whole-container restarts. Defaults `wait_healthy`
to true because a restart that doesn't wait is almost always a footgun in
test workflows — subsequent steps would race the node's startup.
"""

import asyncio
import time
from typing import Any

import aiohttp

from merobox.commands.bootstrap.steps._docker_utils import (
    is_binary_mode,
    resolve_container,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import HEALTH_CHECK_TIMEOUT, NODE_READY_TIMEOUT
from merobox.commands.utils import console, get_node_rpc_url


class RestartContainerStep(BaseStep):
    """Restart a node container with `docker restart`, optionally awaiting health."""

    def _get_required_fields(self) -> list[str]:
        return ["container"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("container")

        step_name = self._get_step_name()
        if "wait_healthy" in self.config and not isinstance(
            self.config["wait_healthy"], bool
        ):
            raise ValueError(f"Step '{step_name}': 'wait_healthy' must be a boolean")

        if "timeout" in self.config:
            timeout = self.config["timeout"]
            if not isinstance(timeout, int) or timeout <= 0:
                raise ValueError(
                    f"Step '{step_name}': 'timeout' must be a positive integer"
                )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping restart_container: --no-docker mode does not "
                "support container restart[/yellow]"
            )
            return True

        container_name = self._resolve_dynamic_value(
            self.config["container"], workflow_results, dynamic_values
        )
        wait_healthy = self.config.get("wait_healthy", True)
        timeout = self.config.get("timeout", NODE_READY_TIMEOUT)

        console.print(f"[yellow]Restarting container {container_name}...[/yellow]")

        container = resolve_container(self.manager, container_name)
        if container is None:
            return False

        try:
            container.restart()
        except Exception as exc:
            console.print(f"[red]✗ Failed to restart {container_name}: {exc}[/red]")
            return False

        console.print(f"[green]✓ Restarted {container_name}[/green]")

        if not wait_healthy:
            return True

        return await self._wait_healthy(container_name, timeout)

    async def _wait_healthy(self, container_name: str, timeout: int) -> bool:
        """Poll the node's admin /health endpoint until ready or timeout.

        The caller asked for health verification (wait_healthy=true), so if we
        can't even resolve the RPC URL the step has to fail — silently passing
        would mask a node-unavailability bug.
        """
        try:
            rpc_url = get_node_rpc_url(container_name, self.manager)
        except Exception as exc:
            console.print(
                f"[red]✗ Could not resolve RPC URL for {container_name}: "
                f"{exc}[/red]"
            )
            return False

        deadline = time.time() + timeout
        console.print(
            f"[cyan]Waiting up to {timeout}s for {container_name} to be healthy...[/cyan]"
        )

        async with aiohttp.ClientSession() as session:
            while time.time() < deadline:
                try:
                    async with session.get(
                        f"{rpc_url}/admin-api/health",
                        timeout=HEALTH_CHECK_TIMEOUT,
                    ) as response:
                        if response.status == 200:
                            console.print(f"[green]✓ {container_name} healthy[/green]")
                            return True
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(1)

        console.print(
            f"[red]✗ {container_name} did not become healthy within {timeout}s[/red]"
        )
        return False
