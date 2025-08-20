"""
Wait step executor.
"""

import asyncio
from typing import Dict, Any
from ...utils import console
from .base import BaseStep


class WaitStep(BaseStep):
    """Execute a wait step."""
    
    async def execute(self, workflow_results: Dict[str, Any], dynamic_values: Dict[str, Any]) -> bool:
        wait_seconds = self.config.get('seconds', 5)
        console.print(f"Waiting {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)
        return True
