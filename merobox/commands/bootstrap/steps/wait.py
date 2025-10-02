"""
Wait step executor.
"""

import asyncio
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class WaitStep(BaseStep):
    """Execute a wait step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return []  # No required fields, seconds defaults to 5

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate seconds is an integer if provided
        if "seconds" in self.config and not isinstance(self.config["seconds"], int):
            raise ValueError(f"Step '{step_name}': 'seconds' must be an integer")

        # Validate seconds is positive if provided
        if "seconds" in self.config and self.config["seconds"] <= 0:
            raise ValueError(
                f"Step '{step_name}': 'seconds' must be a positive integer"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        wait_seconds = self.config.get("seconds", 5)
        console.print(f"Waiting {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)
        return True
