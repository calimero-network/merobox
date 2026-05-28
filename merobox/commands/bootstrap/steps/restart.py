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

# Directory where pre/post-restart log snapshots get written. CI
# workflows that want them archived as artifacts should configure
# their upload step to glob this directory. The default matches the
# convention `e2e-rust-apps.yml`'s log-watcher uses (`docker-logs/`),
# so a CI run that already uploads the watcher's output will also
# pick up the snapshots automatically.
#
# The primary env var was originally `MEROBOX_PRE_RESTART_LOG_DIR`
# back when the step only captured the pre-restart phase. Now that
# it captures BOTH phases into the same directory, the canonical
# name is `MEROBOX_RESTART_LOG_DIR`. The old name remains as a
# backward-compat alias so anyone who set it in their CI before
# 0.6.23 doesn't have to update simultaneously; the new name takes
# precedence if both are set.
_RESTART_LOG_DIR_ENV = "MEROBOX_RESTART_LOG_DIR"
_RESTART_LOG_DIR_ENV_LEGACY = "MEROBOX_PRE_RESTART_LOG_DIR"
_RESTART_LOG_DIR_DEFAULT = "docker-logs"


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

        # Re-inject the NAT-topology default route if applicable.
        # `docker restart` resets the container's network
        # configuration to Docker's defaults, which wipes the
        # `ip route replace default via <gateway-IP>` override
        # that the topology setup applied via
        # `inject_default_route_into_client`. Without re-injection,
        # post-restart packets go via Docker's bridge gateway
        # instead of the NAT gateway — they leave fine but the
        # MASQUERADE return path is missing, so noise handshakes
        # to the boot-node time out (verified via netns
        # route-watcher in the #2469 keypair-repro v6 run; see
        # the PR description for the timeline).
        #
        # Best-effort: failure to re-inject doesn't fail the
        # restart itself (the caller may not even be in a NAT
        # topology — `nat_topology_state` is `None` in that
        # case). Errors are surfaced to the console.
        self._reinject_nat_default_route(container_name)

        if not wait_healthy:
            # No health gate requested. Snapshot what the new
            # incarnation has logged so far — best-effort, since
            # the container may still be very early in startup.
            self._snapshot_post_restart_logs(container, container_name)
            return True

        healthy = await self._wait_healthy(container_name, timeout)
        # Snapshot post-restart logs once the container is up
        # (or after the wait-healthy timeout — either way, we
        # want to capture whatever's been written). The OLD
        # `docker logs -f` follower in the CI watcher doesn't
        # follow across `docker restart`'s stop+start cycle: it
        # exits when the pre-restart container's log stream
        # closes during the stop phase, and the watcher loop
        # treats the name as already-tracked so it never
        # reattaches. Without this dump, the post-restart
        # incarnation's logs never make it to a CI artifact and
        # any investigation of post-restart behaviour is
        # blind — see merobox#258 (the pre-restart half of this
        # capture) for the upstream rationale.
        self._snapshot_post_restart_logs(container, container_name)
        return healthy

    def _reinject_nat_default_route(self, container_name: str) -> None:
        """Re-inject the NAT-gateway default route into the
        just-restarted container if it belongs to a live NAT
        topology.

        The `nat_topology_state` attribute is stashed on the
        manager by `WorkflowExecutor._start_nodes_nat_topology`
        when the topology comes up, and cleared on teardown. If
        absent or empty, the restart isn't in a NAT context and
        this method is a no-op.
        """
        state = getattr(self.manager, "nat_topology_state", None)
        if state is None:
            # Not a NAT topology — nothing to re-inject.
            return
        if container_name not in state.client_names:
            # Restart targets a container that isn't a NAT
            # client (e.g. the boot-node or a non-topology
            # container). Default route is already correct.
            return

        # Imported here to avoid pulling the NAT module's
        # docker / iptables deps into the step's import path
        # when the step is used outside NAT contexts.
        from merobox.topology.nat import (
            inject_default_route_into_client,
            wait_for_client_reachability,
        )

        client = self.manager.client
        try:
            inject_default_route_into_client(client, state, container_name)
        except Exception as exc:
            console.print(
                f"[yellow]Failed to re-inject default route into "
                f"{container_name!r} post-restart: {exc}[/yellow]"
            )
            return

        # Verify the client can still reach the boot-node
        # through the gateway. This is the same probe the
        # initial topology setup runs and serves the same
        # purpose: catch a half-broken topology before the
        # caller starts asserting on sync behaviour. A failure
        # here means the route was re-injected but the
        # forwarding path is broken (e.g. gateway MASQUERADE
        # rule got dropped somehow) — much more diagnostic
        # than letting downstream sync time out.
        try:
            wait_for_client_reachability(client, state, container_name)
        except Exception as exc:
            console.print(
                f"[yellow]Post-restart reachability check from "
                f"{container_name!r} → boot-node failed: {exc}[/yellow]"
            )

    @staticmethod
    def _snapshot_pre_restart_logs(container: Any, container_name: str) -> None:
        """Pre-restart dump — captures the to-be-killed container's
        logs before `docker restart` cycles it. See `_snapshot_logs`
        for the shared shape; this is a thin wrapper that pins the
        phase to ``pre-restart``."""
        RestartContainerStep._snapshot_logs(
            container, container_name, phase="pre-restart"
        )

    @staticmethod
    def _snapshot_post_restart_logs(container: Any, container_name: str) -> None:
        """Post-restart dump — captures the freshly-restarted
        container's logs after `docker restart` returns (and after
        `wait_healthy` succeeds, if it was requested).

        The CI watcher (`docker logs -f` keyed on container NAME)
        DOES NOT follow across the restart's stop+start cycle —
        the follower exits when the pre-restart container's log
        stream closes during stop, and the watcher loop sees the
        name as already-tracked so it never reattaches. Without
        this dump, the post-restart incarnation's logs go
        unrecorded entirely.

        Reloads the container's attrs first via `container.reload()`
        because docker-py caches state on the Python-side object.
        `docker restart` cycles the underlying runtime, so a stale
        Python container handle returned from before the restart
        could in principle hand back the wrong incarnation's logs
        (in practice docker-py's `logs()` re-queries by ID/name on
        each call, but the reload is cheap insurance against future
        SDK behavior drift).
        """
        try:
            container.reload()
        except Exception as exc:
            # Don't fail the snapshot just because reload glitched;
            # docker-py's `logs()` re-queries on each call anyway,
            # so even a stale Python handle gives us the right
            # logs. Log + proceed.
            console.print(
                f"[yellow]Could not reload {container_name!r} before "
                f"post-restart snapshot: {exc} (continuing)[/yellow]"
            )
        RestartContainerStep._snapshot_logs(
            container, container_name, phase="post-restart"
        )

    @staticmethod
    def _snapshot_logs(container: Any, container_name: str, *, phase: str) -> None:
        """Best-effort dump of the container's logs to a timestamped
        file. Pre- and post-restart entry points share this body so
        the failure-handling and naming convention stay in lockstep.

        Naming convention: ``<dir>/<safe_name>.<phase>-<utc_ts>.log``
        where:
        - ``<dir>`` is ``$MEROBOX_RESTART_LOG_DIR`` (canonical) or
          the legacy ``$MEROBOX_PRE_RESTART_LOG_DIR`` alias, falling
          back to ``docker-logs/`` in the current working directory;
        - ``<safe_name>`` is the container name reduced to its
          basename component, defending against a `container:`
          field that resolved (through dynamic-value substitution)
          to a path-traversal-shaped string. Docker container names
          are already constrained to ``[a-zA-Z0-9][a-zA-Z0-9_.-]*``
          so this guard is belt-and-suspenders, but keeping the
          filesystem-write safe locally is much cheaper than
          tracking the trust boundary across the workflow YAML +
          dynamic-value layer;
        - ``<utc_ts>`` is microsecond-precision so rapid back-to-
          back snapshots (e.g. pre+post within one restart) land
          in distinct files, and ordering between paired snapshots
          is preserved by name.

        Errors at any step (docker API hiccup, can't create
        directory, write fails) are logged to the console but
        never raised — the caller's restart flow must proceed
        regardless of whether the snapshot succeeds.
        """
        log_dir = os.environ.get(
            _RESTART_LOG_DIR_ENV,
            os.environ.get(_RESTART_LOG_DIR_ENV_LEGACY, _RESTART_LOG_DIR_DEFAULT),
        )
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as exc:
            console.print(
                f"[yellow]Could not create {phase} log dir {log_dir!r}: "
                f"{exc} (continuing without snapshot)[/yellow]"
            )
            return

        # UTC + microsecond precision; the µs matter because rapid
        # back-to-back snapshots (e.g. pre+post within one restart)
        # need to land in distinct files.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # `basename` reduces any path-traversal-shaped container
        # name (e.g. "../foo") to a single filesystem component.
        safe_name = os.path.basename(container_name) or "unnamed"
        log_path = os.path.join(log_dir, f"{safe_name}.{phase}-{stamp}.log")

        try:
            # `timestamps=True` so each line carries the docker-side
            # wall-clock timestamp, matching the format the CI
            # watcher writes for the live-streamed container logs.
            log_bytes = container.logs(timestamps=True)
        except Exception as exc:
            console.print(
                f"[yellow]Could not read {phase} logs for "
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
                f"[yellow]Could not write {phase} log file "
                f"{log_path!r}: {exc} (continuing)[/yellow]"
            )
            return

        console.print(
            f"[cyan]Snapshotted {phase} logs of {container_name!r} → {log_path}[/cyan]"
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
