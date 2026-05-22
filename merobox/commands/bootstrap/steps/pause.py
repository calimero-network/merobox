"""Pause / unpause step executors.

Wraps `docker pause` and `docker unpause`. Used to simulate process freezes
that real-world clients hit (laptop sleep/wake, Tauri App Nap). Keep the
two operations separate so workflows compose with `wait` for the freeze
duration instead of a magic auto-resume.
"""

from typing import Any

from merobox.commands.bootstrap.steps._docker_utils import (
    is_binary_mode,
    resolve_container,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class PauseContainerStep(BaseStep):
    """Pause a node container with `docker pause` (SIGSTOP equivalent)."""

    def _get_required_fields(self) -> list[str]:
        return ["container"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("container")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping pause_container: --no-docker mode does not "
                "support container pause[/yellow]"
            )
            return True

        container_name = self._resolve_dynamic_value(
            self.config["container"], workflow_results, dynamic_values
        )
        console.print(f"[yellow]Pausing container {container_name}...[/yellow]")

        container = resolve_container(self.manager, container_name)
        if container is None:
            return False

        try:
            container.pause()
        except Exception as exc:
            console.print(f"[red]✗ Failed to pause {container_name}: {exc}[/red]")
            return False

        console.print(f"[green]✓ Paused {container_name}[/green]")
        return True


class UnpauseContainerStep(BaseStep):
    """Resume a paused node container with `docker unpause`."""

    def _get_required_fields(self) -> list[str]:
        return ["container"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("container")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping unpause_container: --no-docker mode does not "
                "support container pause[/yellow]"
            )
            return True

        container_name = self._resolve_dynamic_value(
            self.config["container"], workflow_results, dynamic_values
        )
        console.print(f"[yellow]Unpausing container {container_name}...[/yellow]")

        container = resolve_container(self.manager, container_name)
        if container is None:
            return False

        try:
            container.unpause()
        except Exception as exc:
            console.print(f"[red]✗ Failed to unpause {container_name}: {exc}[/red]")
            return False

        console.print(f"[green]✓ Unpaused {container_name}[/green]")
        return True
