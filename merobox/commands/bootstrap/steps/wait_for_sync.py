"""
Wait for sync step executor - Waits for nodes to reach consensus on root hash.
"""

import asyncio
import time
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.utils import console, get_node_rpc_url


class WaitForSyncStep(BaseStep):
    """Execute a wait for sync step that verifies root hash consensus across nodes."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["context_id", "nodes"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate context_id is a string
        if "context_id" in self.config and not isinstance(
            self.config["context_id"], str
        ):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        # Validate nodes is a list
        if "nodes" in self.config:
            if not isinstance(self.config["nodes"], list):
                raise ValueError(f"Step '{step_name}': 'nodes' must be a list")
            if len(self.config["nodes"]) < 2:
                raise ValueError(
                    f"Step '{step_name}': 'nodes' must contain at least two nodes for consensus verification"
                )
            for node in self.config["nodes"]:
                if not isinstance(node, str):
                    raise ValueError(
                        f"Step '{step_name}': all items in 'nodes' must be strings"
                    )

            unique_nodes = set(self.config["nodes"])
            if len(unique_nodes) < len(self.config["nodes"]):
                raise ValueError(
                    f"Step '{step_name}': 'nodes' must contain unique node names"
                )
            if len(unique_nodes) < 2:
                raise ValueError(
                    f"Step '{step_name}': 'nodes' must contain at least two unique nodes for consensus verification"
                )

        # Validate timeout is a positive integer if provided
        if "timeout" in self.config:
            if not isinstance(self.config["timeout"], int):
                raise ValueError(f"Step '{step_name}': 'timeout' must be an integer")
            if self.config["timeout"] <= 0:
                raise ValueError(
                    f"Step '{step_name}': 'timeout' must be a positive integer"
                )

        # Validate check_interval is positive if provided
        if "check_interval" in self.config:
            if (
                not isinstance(self.config["check_interval"], (int, float))
                or self.config["check_interval"] <= 0
            ):
                raise ValueError(
                    f"Step '{step_name}': 'check_interval' must be a positive number"
                )

        # Validate retry_attempts is a positive integer if provided
        if "retry_attempts" in self.config:
            if not isinstance(self.config["retry_attempts"], int):
                raise ValueError(
                    f"Step '{step_name}': 'retry_attempts' must be an integer"
                )
            if self.config["retry_attempts"] <= 0:
                raise ValueError(
                    f"Step '{step_name}': 'retry_attempts' must be a positive integer"
                )

        # Validate trigger_sync is a boolean if provided
        if "trigger_sync" in self.config and not isinstance(
            self.config["trigger_sync"], bool
        ):
            raise ValueError(f"Step '{step_name}': 'trigger_sync' must be a boolean")

    async def _fetch_root_hash(
        self, node_name: str, context_id: str, trigger_sync: bool = False
    ) -> tuple[str, str | None]:
        """
        Fetch root hash for a context from a specific node with retry logic.

        Args:
            node_name: Name of the node to query
            context_id: Context ID to get root hash for
            trigger_sync: Whether to trigger sync before fetching hash

        Returns:
            Tuple of (node_name, root_hash) or (node_name, None) if error
        """
        max_retries = 3
        retry_delay = 0.5  # 500ms between retries

        for retry in range(max_retries):
            try:
                # Add small delay on retries to avoid overwhelming the API
                if retry > 0:
                    await asyncio.sleep(retry_delay)

                rpc_url = get_node_rpc_url(node_name, self.manager)
                client = get_client_for_rpc_url(rpc_url)

                # Optionally trigger sync to ensure root_hash is up-to-date
                if trigger_sync:
                    try:
                        client.sync_context(context_id)
                    except (RuntimeError, ValueError, AttributeError):
                        # Fallback to sync_all_contexts if sync_context fails
                        try:
                            client.sync_all_contexts()
                        except (RuntimeError, ValueError, AttributeError) as sync_error:
                            console.print(
                                f"[dim]⚠️  Sync trigger failed for {node_name}: {str(sync_error)}[/dim]"
                            )

                # Get context information which includes root_hash
                context_data = client.get_context(context_id)

                # Extract root hash from response
                root_hash = None

                if isinstance(context_data, dict) and "data" in context_data:
                    context = context_data["data"]
                    if isinstance(context, dict):
                        # The API returns camelCase 'rootHash'
                        root_hash = context.get("rootHash") or context.get("root_hash")

                if root_hash is not None:
                    return node_name, str(root_hash)

                # No root hash found - retry
                if retry < max_retries - 1:
                    console.print(
                        f"[dim]⚠️  No root_hash from {node_name}, retrying ({retry + 1}/{max_retries})...[/dim]"
                    )
                    continue

                return node_name, None

            except (
                RuntimeError,
                ValueError,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as e:
                if retry < max_retries - 1:
                    console.print(
                        f"[dim]⚠️  Error fetching from {node_name}: {str(e)}, retrying ({retry + 1}/{max_retries})...[/dim]"
                    )
                    continue
                else:
                    console.print(
                        f"[yellow]⚠️  Failed to get root_hash from {node_name} after {max_retries} retries: {str(e)}[/yellow]"
                    )
                    return node_name, None

        return node_name, None

    async def _wait_for_sync(
        self,
        nodes: list[str],
        context_id: str,
        timeout: int,
        check_interval: float,
        retry_attempts: int | None = None,
        trigger_sync: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Wait for all nodes to reach the same root hash for a given context.

        Args:
            nodes: List of node names to check
            context_id: Context ID to verify sync for
            timeout: Maximum seconds to wait for sync
            check_interval: Seconds between sync checks
            retry_attempts: Optional max number of attempts (overrides timeout if both set)
            trigger_sync: Whether to trigger sync on each node before checking

        Returns:
            Tuple of (synced: bool, details: dict with sync information)
        """
        start_time = time.time()
        attempt = 0
        max_attempts = retry_attempts or float("inf")

        console.print(
            f"[cyan]🔄 Waiting for {len(nodes)} node(s) to sync on context {context_id}...[/cyan]"
        )
        console.print(f"[dim]  Nodes: {', '.join(nodes)}[/dim]")
        console.print(
            f"[dim]  Timeout: {timeout}s, Check interval: {check_interval}s"
            f"{', Trigger sync: enabled' if trigger_sync else ''}[/dim]"
        )

        while (time.time() - start_time < timeout) and (attempt < max_attempts):
            attempt += 1
            elapsed = time.time() - start_time

            # Add small random jitter to avoid synchronized request spikes
            if attempt > 1:
                jitter = 0.1 * (attempt % 3)  # 0-200ms jitter
                await asyncio.sleep(jitter)

            # Fetch root hash from all nodes concurrently
            tasks = [
                self._fetch_root_hash(node, context_id, trigger_sync) for node in nodes
            ]
            results = await asyncio.gather(*tasks)

            # Build hash map
            root_hashes = dict(results)

            # Check if any nodes failed to respond
            failed_nodes = [
                node for node, hash_val in root_hashes.items() if hash_val is None
            ]
            if failed_nodes:
                console.print(
                    f"[yellow]Attempt {attempt} ({elapsed:.1f}s): "
                    f"Failed to get hash from {len(failed_nodes)} node(s): {', '.join(failed_nodes)}[/yellow]"
                )
                await asyncio.sleep(check_interval)
                continue

            # Get unique hashes (excluding None values)
            valid_hashes = {h for h in root_hashes.values() if h is not None}

            if len(valid_hashes) == 1:
                # All nodes have the same root hash - SUCCESS!
                synced_hash = list(valid_hashes)[0]
                elapsed = time.time() - start_time

                console.print(
                    f"[green]✓ All nodes synced after {elapsed:.2f}s ({attempt} attempts)![/green]"
                )
                console.print(f"[green]  Root hash: {synced_hash}[/green]")

                return True, {
                    "synced": True,
                    "root_hash": synced_hash,
                    "elapsed_seconds": round(elapsed, 2),
                    "attempts": attempt,
                    "node_hashes": root_hashes,
                }

            # Hashes don't match yet
            console.print(
                f"[yellow]Attempt {attempt} ({elapsed:.1f}s): "
                f"Root hashes don't match - {len(valid_hashes)} unique hash(es) found[/yellow]"
            )

            # Show details of mismatched hashes
            hash_groups = {}
            for node, hash_val in root_hashes.items():
                if hash_val not in hash_groups:
                    hash_groups[hash_val] = []
                hash_groups[hash_val].append(node)

            for hash_val, node_list in hash_groups.items():
                console.print(f"[dim]  {hash_val}: {', '.join(node_list)}[/dim]")

            # Wait before next check
            await asyncio.sleep(check_interval)

        # Timeout exceeded or max attempts reached
        elapsed = time.time() - start_time
        console.print(
            f"[red]✗ Sync verification failed after {elapsed:.2f}s ({attempt} attempts)[/red]"
        )

        # Get final state
        tasks = [self._fetch_root_hash(node, context_id) for node in nodes]
        results = await asyncio.gather(*tasks)
        final_hashes = dict(results)

        console.print("[red]  Final state:[/red]")
        for node, hash_val in final_hashes.items():
            console.print(f"[red]    {node}: {hash_val or 'N/A'}[/red]")

        return False, {
            "synced": False,
            "error": (
                "Sync timeout exceeded"
                if attempt < max_attempts
                else "Max attempts reached"
            ),
            "timeout": timeout,
            "elapsed_seconds": round(elapsed, 2),
            "attempts": attempt,
            "final_hashes": final_hashes,
        }

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """
        Execute the wait for sync step.

        Args:
            workflow_results: Results from previous workflow steps
            dynamic_values: Dynamic values captured from previous steps

        Returns:
            True if all nodes synced successfully, False otherwise
        """
        # Resolve dynamic values
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        nodes = self.config["nodes"]
        timeout = self.config.get("timeout", 30)
        check_interval = self.config.get("check_interval", 1.0)
        retry_attempts = self.config.get("retry_attempts")
        # Default: enabled - uses sync_context(context_id) for targeted sync
        trigger_sync = self.config.get("trigger_sync", True)

        console.print("\n[bold cyan]⏳ Waiting for node synchronization...[/bold cyan]")

        # Execute sync wait
        synced, details = await self._wait_for_sync(
            nodes, context_id, timeout, check_interval, retry_attempts, trigger_sync
        )

        # Export details if outputs are configured
        if "outputs" in self.config:
            self._export_custom_outputs(details, "", dynamic_values)

        # Return success/failure
        return synced
