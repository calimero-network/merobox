"""
Wait for sync step executor — waits for nodes to converge on context state
hash, group governance state hash, or both.
"""

import asyncio
import time
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import SYNC_RETRY_ATTEMPTS, SYNC_RETRY_DELAY
from merobox.commands.utils import console


def _build_success_details(
    targets: list[dict],
    converged_hashes: dict[str, str],
    per_target_state: dict[str, dict[str, str | None]],
    elapsed: float,
    attempt: int,
) -> dict[str, Any]:
    """Build the success result dict with backwards-compatible top-level keys.

    For each target kind that converged, expose the hash at a stable
    top-level key for downstream ``outputs:`` references:
    * context target → ``context_state_hash`` (and legacy ``root_hash``
      mirror, for workflows authored before the rename)
    * group target → ``group_state_hash``

    The ``targets`` map remains the canonical source of all per-target
    hashes when multiple were specified.
    """
    details: dict[str, Any] = {
        "synced": True,
        "targets": converged_hashes,
        "elapsed_seconds": round(elapsed, 2),
        "attempts": attempt,
        "per_target_node_hashes": per_target_state,
    }
    for target in targets:
        label = f"{target['kind']}={target['id']}"
        hash_val = converged_hashes.get(label)
        if hash_val is None:
            continue
        if target["kind"] == "context":
            # Legacy alias for workflows that read "root_hash"; safe to
            # remove once all callers migrate to context_state_hash.
            details["root_hash"] = hash_val
            details["context_state_hash"] = hash_val
        elif target["kind"] == "group":
            details["group_state_hash"] = hash_val
    return details


class WaitForSyncStep(BaseStep):
    """Wait for nodes to converge on context state and/or group governance state.

    Specify ``context_id`` to wait for ``contextStateHash`` to converge across
    nodes (CRDT storage state for that context). Specify ``group_id`` to wait
    for ``groupStateHash`` to converge (governance state for that group).
    Specify both to wait for both — useful for tests that change governance
    and expect state effects (e.g. removed member, verify their writes don't
    leak). At least one of ``context_id`` / ``group_id`` is required.
    """

    def _get_required_fields(self) -> list[str]:
        # context_id and group_id are both optional; at least one is required,
        # validated explicitly in _validate_field_types below.
        return ["nodes"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # `key in dict` is True for `key: null` too — treat None values as
        # equivalent to "not specified" so the "at least one" guard
        # properly catches `context_id: null` configs.
        has_context_id = self.config.get("context_id") is not None
        has_group_id = self.config.get("group_id") is not None

        if not has_context_id and not has_group_id:
            raise ValueError(
                f"Step '{step_name}': at least one of 'context_id' or 'group_id' must be specified"
            )

        # Validate context_id is a string
        if has_context_id and not isinstance(self.config["context_id"], str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        # Validate group_id is a string
        if has_group_id and not isinstance(self.config["group_id"], str):
            raise ValueError(f"Step '{step_name}': 'group_id' must be a string")

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

    async def _fetch_hash(
        self,
        target: dict,
        node_name: str,
        trigger_sync: bool = False,
    ) -> tuple[str, str | None]:
        """
        Fetch a state hash for the given target (context or group) from a
        specific node, with retry logic.

        Args:
            target: dict with keys ``kind`` (\"context\" or \"group\"),
                ``id`` (the context_id or group_id), and ``field`` (the
                JSON field to read from the response — ``contextStateHash``
                or ``groupStateHash``).
            node_name: Name of the node to query.
            trigger_sync: Whether to trigger sync before fetching (only
                meaningful for context targets; ignored for group targets).

        Returns:
            Tuple of (node_name, hash) or (node_name, None) on error.
        """
        max_retries = SYNC_RETRY_ATTEMPTS
        retry_delay = SYNC_RETRY_DELAY
        kind = target["kind"]
        target_id = target["id"]
        field = target["field"]

        if kind not in ("context", "group"):
            console.print(
                f"[red]Internal error: unknown wait_for_sync target kind '{kind}'[/red]"
            )
            return node_name, None

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return node_name, None

        for retry in range(max_retries):
            try:
                if retry > 0:
                    await asyncio.sleep(retry_delay)

                client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)

                # Optionally trigger sync (context-state target only — group
                # governance state has its own propagation path).
                if trigger_sync and kind == "context":
                    try:
                        client.sync_context(target_id)
                    except (RuntimeError, ValueError, AttributeError):
                        try:
                            client.sync_all_contexts()
                        except (RuntimeError, ValueError, AttributeError) as sync_error:
                            console.print(
                                f"[dim]⚠️  Sync trigger failed for {node_name}: {str(sync_error)}[/dim]"
                            )

                # Fetch the right info object based on kind.
                if kind == "context":
                    response = client.get_context(target_id)
                else:
                    response = client.get_group_info(target_id)

                # Extract hash from response.data.<field>. For context
                # targets we also fall back to the legacy `rootHash` field
                # name so this code works against released calimero
                # binaries that pre-date the contextStateHash rename
                # (transitional — can be cleaned up after the rename has
                # shipped in a calimero release).
                value = None
                if isinstance(response, dict) and "data" in response:
                    body = response["data"]
                    if isinstance(body, dict):
                        value = body.get(field)
                        if value is None and kind == "context":
                            value = body.get("rootHash")

                if value is not None:
                    return node_name, str(value)

                if retry < max_retries - 1:
                    console.print(
                        f"[dim]⚠️  No {field} from {node_name} ({kind} {target_id}), "
                        f"retrying ({retry + 1}/{max_retries})...[/dim]"
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
                        f"[dim]⚠️  Error fetching {field} from {node_name}: {str(e)}, "
                        f"retrying ({retry + 1}/{max_retries})...[/dim]"
                    )
                    continue
                else:
                    console.print(
                        f"[yellow]⚠️  Failed to get {field} from {node_name} after {max_retries} retries: {str(e)}[/yellow]"
                    )
                    return node_name, None

        return node_name, None

    async def _check_target_convergence(
        self,
        target: dict,
        nodes: list[str],
        trigger_sync: bool,
    ) -> tuple[bool, dict[str, str | None]]:
        """Fetch the target's hash from all nodes; return (converged, per-node mapping).

        Converged iff every node returned a hash and all hashes match.
        """
        tasks = [self._fetch_hash(target, node, trigger_sync) for node in nodes]
        results = await asyncio.gather(*tasks)
        node_hashes = dict(results)

        # All nodes must have a value, and all values must agree.
        if any(h is None for h in node_hashes.values()):
            return False, node_hashes
        unique = {h for h in node_hashes.values() if h is not None}
        return len(unique) == 1, node_hashes

    @staticmethod
    def _target_label(target: dict) -> str:
        """Human-readable label for log lines."""
        return f"{target['kind']}={target['id']}"

    async def _wait_for_sync(
        self,
        targets: list[dict],
        nodes: list[str],
        timeout: int,
        check_interval: float,
        retry_attempts: int | None = None,
        trigger_sync: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Wait for all targets to converge across all nodes.

        A target converges when every node returns the same hash for it.
        All specified targets must converge for the overall result to succeed.
        """
        start_time = time.time()
        attempt = 0
        max_attempts = retry_attempts or float("inf")

        target_labels = ", ".join(self._target_label(t) for t in targets)
        console.print(
            f"[cyan]🔄 Waiting for {len(nodes)} node(s) to sync on {len(targets)} target(s): {target_labels}...[/cyan]"
        )
        console.print(f"[dim]  Nodes: {', '.join(nodes)}[/dim]")
        console.print(
            f"[dim]  Timeout: {timeout}s, Check interval: {check_interval}s"
            f"{', Trigger sync: enabled' if trigger_sync else ''}[/dim]"
        )

        last_per_target: dict[str, dict[str, str | None]] = {}

        while (time.time() - start_time < timeout) and (attempt < max_attempts):
            attempt += 1
            elapsed = time.time() - start_time

            if attempt > 1:
                jitter = 0.1 * (attempt % 3)
                await asyncio.sleep(jitter)

            # Check each target's convergence in parallel.
            target_checks = await asyncio.gather(
                *(
                    self._check_target_convergence(target, nodes, trigger_sync)
                    for target in targets
                )
            )

            all_converged = True
            per_target_state: dict[str, dict[str, str | None]] = {}
            converged_hashes: dict[str, str] = {}

            for target, (converged, node_hashes) in zip(targets, target_checks):
                label = self._target_label(target)
                per_target_state[label] = node_hashes
                if converged:
                    # Take the (only) unique value
                    converged_hashes[label] = next(iter(node_hashes.values()))
                else:
                    all_converged = False

            last_per_target = per_target_state

            if all_converged:
                console.print(
                    f"[green]✓ All targets synced after {elapsed:.2f}s ({attempt} attempts)![/green]"
                )
                for label, hash_val in converged_hashes.items():
                    console.print(f"[green]  {label}: {hash_val}[/green]")

                return True, _build_success_details(
                    targets,
                    converged_hashes,
                    per_target_state,
                    elapsed,
                    attempt,
                )

            # Report what didn't converge yet.
            console.print(
                f"[yellow]Attempt {attempt} ({elapsed:.1f}s): {len(targets)} target(s) checked, not all converged yet[/yellow]"
            )
            for target, (converged, node_hashes) in zip(targets, target_checks):
                label = self._target_label(target)
                if converged:
                    console.print(f"[dim]  ✓ {label} converged[/dim]")
                    continue
                failed = [n for n, h in node_hashes.items() if h is None]
                if failed:
                    console.print(
                        f"[dim]  · {label}: missing from {len(failed)} node(s): {', '.join(failed)}[/dim]"
                    )
                else:
                    hash_groups: dict[str, list[str]] = {}
                    for n, h in node_hashes.items():
                        hash_groups.setdefault(h, []).append(n)
                    console.print(
                        f"[dim]  · {label}: {len(hash_groups)} unique hash(es)[/dim]"
                    )
                    for h, ns in hash_groups.items():
                        console.print(f"[dim]      {h}: {', '.join(ns)}[/dim]")

            await asyncio.sleep(check_interval)

        # Timeout / max attempts reached — final consistency check.
        elapsed = time.time() - start_time
        target_checks = await asyncio.gather(
            *(
                self._check_target_convergence(target, nodes, False)
                for target in targets
            )
        )

        all_converged = True
        per_target_state = {}
        converged_hashes = {}
        for target, (converged, node_hashes) in zip(targets, target_checks):
            label = self._target_label(target)
            per_target_state[label] = node_hashes
            if converged:
                converged_hashes[label] = next(iter(node_hashes.values()))
            else:
                all_converged = False
        last_per_target = per_target_state

        if all_converged:
            console.print(
                f"[green]✓ All targets synced after {elapsed:.2f}s ({attempt} attempts, verified on final check)[/green]"
            )
            for label, hash_val in converged_hashes.items():
                console.print(f"[green]  {label}: {hash_val}[/green]")
            return True, _build_success_details(
                targets,
                converged_hashes,
                last_per_target,
                elapsed,
                attempt,
            )

        console.print(
            f"[red]✗ Sync verification failed after {elapsed:.2f}s ({attempt} attempts)[/red]"
        )
        console.print("[red]  Final state:[/red]")
        for label, node_hashes in last_per_target.items():
            console.print(f"[red]    {label}:[/red]")
            for node, hash_val in node_hashes.items():
                console.print(f"[red]      {node}: {hash_val or 'N/A'}[/red]")

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
            "per_target_node_hashes": last_per_target,
        }

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """Execute the wait for sync step."""
        # Resolve dynamic values for whichever IDs are present.
        targets: list[dict] = []
        if "context_id" in self.config:
            context_id = self._resolve_dynamic_value(
                self.config["context_id"], workflow_results, dynamic_values
            )
            targets.append(
                {"kind": "context", "id": context_id, "field": "contextStateHash"}
            )
        if "group_id" in self.config:
            group_id = self._resolve_dynamic_value(
                self.config["group_id"], workflow_results, dynamic_values
            )
            targets.append({"kind": "group", "id": group_id, "field": "groupStateHash"})

        nodes = self.config["nodes"]
        timeout = self.config.get("timeout", 30)
        check_interval = self.config.get("check_interval", 0.5)
        retry_attempts = self.config.get("retry_attempts")
        # Default: enabled — uses sync_context for context targets.
        trigger_sync = self.config.get("trigger_sync", True)

        console.print("\n[bold cyan]⏳ Waiting for node synchronization...[/bold cyan]")

        synced, details = await self._wait_for_sync(
            targets, nodes, timeout, check_interval, retry_attempts, trigger_sync
        )

        if "outputs" in self.config:
            self._export_custom_outputs(details, "", dynamic_values)

        return synced
