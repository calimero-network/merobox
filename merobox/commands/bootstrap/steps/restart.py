"""Restart-container step executor.

Wraps `docker restart` for whole-container restarts. Defaults `wait_healthy`
to true because a restart that doesn't wait is almost always a footgun in
test workflows — subsequent steps would race the node's startup.

Also dumps the to-be-killed container's logs to a per-restart file before
issuing the restart, so a CI artifact-uploader can capture the pre-restart
incarnation's events. Without this, the CI watcher's `docker logs -f`
follower (which is keyed on container NAME, not container ID) stays
attached to the old container ID after `docker restart` swaps the
underlying container; the new container's logs go uncaptured and any
post-restart investigation is blind. See
`apps/scaffolding-e2e/.github/workflows/e2e-rust-apps.yml`'s log-watcher
loop for the consumer side of this output.
"""

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from merobox.commands.bootstrap.steps._docker_utils import (
    is_binary_mode,
    resolve_container,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import HEALTH_CHECK_TIMEOUT, NODE_READY_TIMEOUT
from merobox.commands.utils import console, get_node_rpc_url

# Directory where pre-restart log snapshots get written. CI workflows
# that want them archived as artifacts should configure their upload
# step to glob this directory. The default matches the convention
# `e2e-rust-apps.yml`'s log-watcher uses (`docker-logs/`), so a CI run
# that already uploads the watcher's output will also pick up the
# pre-restart snapshots automatically.
_PRE_RESTART_LOG_DIR_ENV = "MEROBOX_PRE_RESTART_LOG_DIR"
_PRE_RESTART_LOG_DIR_DEFAULT = "docker-logs"


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

        # Dump the to-be-killed container's logs BEFORE the restart fires
        # so the pre-restart incarnation's events survive in a CI
        # artifact. See module docstring for why the CI watcher alone
        # isn't enough. Best-effort: a failure to capture must not
        # block the restart itself — the operator may have asked for
        # the restart precisely because the container is in a weird
        # state, and refusing to restart would compound the problem.
        self._snapshot_pre_restart_logs(container, container_name)

        try:
            container.restart()
        except Exception as exc:
            console.print(f"[red]✗ Failed to restart {container_name}: {exc}[/red]")
            return False

        console.print(f"[green]✓ Restarted {container_name}[/green]")

        if not wait_healthy:
            return True

        return await self._wait_healthy(container_name, timeout)

    @staticmethod
    def _snapshot_pre_restart_logs(container: Any, container_name: str) -> None:
        """Best-effort dump of the container's logs to a numbered file
        before `docker restart` rotates the underlying container ID.

        Naming convention: ``<dir>/<container>.pre-restart-<utc_ts>.log``
        where ``<dir>`` is ``$MEROBOX_PRE_RESTART_LOG_DIR`` or, by
        default, ``docker-logs/`` in the current working directory.
        The UTC timestamp suffix means multiple restarts of the same
        container in a single workflow produce distinct files (sortable
        by name).

        Errors at any step (docker API hiccup, can't create directory,
        write fails) are logged to the console but never raised — the
        caller's `container.restart()` must run regardless.
        """
        log_dir = os.environ.get(_PRE_RESTART_LOG_DIR_ENV, _PRE_RESTART_LOG_DIR_DEFAULT)
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as exc:
            console.print(
                f"[yellow]Could not create pre-restart log dir {log_dir!r}: "
                f"{exc} (continuing without snapshot)[/yellow]"
            )
            return

        # UTC + millisecond precision; the millis matter because rapid
        # back-to-back restarts in a workflow shouldn't collide.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        log_path = os.path.join(log_dir, f"{container_name}.pre-restart-{stamp}.log")

        try:
            # `timestamps=True` so each line carries the docker-side
            # wall-clock timestamp, matching the format the CI
            # watcher writes for the post-restart container's logs.
            log_bytes = container.logs(timestamps=True)
        except Exception as exc:
            console.print(
                f"[yellow]Could not read pre-restart logs for "
                f"{container_name!r}: {exc} (continuing)[/yellow]"
            )
            return

        if isinstance(log_bytes, bytes):
            log_text = log_bytes.decode("utf-8", errors="replace")
        else:
            log_text = str(log_bytes)

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(log_text)
        except OSError as exc:
            console.print(
                f"[yellow]Could not write pre-restart log file "
                f"{log_path!r}: {exc} (continuing)[/yellow]"
            )
            return

        console.print(
            f"[cyan]Snapshotted pre-restart logs of {container_name!r} → {log_path}[/cyan]"
        )

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
