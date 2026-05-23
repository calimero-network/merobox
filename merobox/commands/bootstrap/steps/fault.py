"""Network fault-injection step executor (loss / delay) via `tc netem`.

Simulates flaky / high-latency links. Hard partition is intentionally not a
`fault:` value here — use `disconnect_node` for that since it's cleaner
(single docker syscall, no in-container exec, no NET_ADMIN cap required).

Requires the target container to carry the NET_ADMIN capability. By default
merobox adds NET_ADMIN to all node containers; if you've opted out via
`network_admin: false` in the workflow's `nodes:` block, `tc` will fail
with EPERM and this step surfaces a clear error pointing to the cause.
"""

import asyncio
import re
from typing import Any

from merobox.commands.bootstrap.steps._docker_utils import (
    is_binary_mode,
    resolve_container,
    safe_console_error,
    warn_if_mdns_enabled,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console

DEFAULT_INTERFACE = "eth0"
SUPPORTED_FAULTS = ("loss", "delay")
# Linux interface naming follows kernel rules: alnum plus a few separators,
# max 15 chars. Restricting the input keeps `tc` argv hygienic — exec_run
# is shell-free so this is defense in depth, not a primary boundary.
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


class InjectNetworkFaultStep(BaseStep):
    """Run `tc qdisc add ... netem ...` inside a container for `duration`, then clear it."""

    def _get_required_fields(self) -> list[str]:
        return ["container", "fault", "duration"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("container")
        self._validate_string_field("fault")

        step_name = self._get_step_name()

        fault = self.config.get("fault")
        if fault not in SUPPORTED_FAULTS:
            raise ValueError(
                f"Step '{step_name}': 'fault' must be one of "
                f"{', '.join(SUPPORTED_FAULTS)} (got '{fault}'). "
                f"For full partition use disconnect_node."
            )

        duration = self.config.get("duration")
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError(
                f"Step '{step_name}': 'duration' must be a positive integer (seconds)"
            )

        if fault == "loss":
            percent = self.config.get("percent")
            if not isinstance(percent, (int, float)) or not (0 < percent <= 100):
                raise ValueError(
                    f"Step '{step_name}': 'percent' is required for fault=loss "
                    f"and must be in (0, 100]"
                )

        if fault == "delay":
            ms = self.config.get("ms")
            if not isinstance(ms, int) or ms <= 0:
                raise ValueError(
                    f"Step '{step_name}': 'ms' is required for fault=delay "
                    f"and must be a positive integer"
                )

        if "interface" in self.config:
            interface = self.config["interface"]
            if not isinstance(interface, str):
                raise ValueError(f"Step '{step_name}': 'interface' must be a string")
            if not _INTERFACE_RE.match(interface):
                raise ValueError(
                    f"Step '{step_name}': 'interface' must match "
                    f"{_INTERFACE_RE.pattern} (Linux interface naming rules)"
                )

    def _build_netem_args(self) -> list[str]:
        fault = self.config["fault"]
        if fault == "loss":
            return ["loss", f"{self.config['percent']}%"]
        return ["delay", f"{self.config['ms']}ms"]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping inject_network_fault: --no-docker mode has "
                "no container to exec tc into[/yellow]"
            )
            return True

        container_name = self._resolve_dynamic_value(
            self.config["container"], workflow_results, dynamic_values
        )
        interface = self.config.get("interface", DEFAULT_INTERFACE)
        duration = self.config["duration"]
        netem_args = self._build_netem_args()

        container = resolve_container(self.manager, container_name)
        if container is None:
            return False

        warn_if_mdns_enabled(container, container_name)

        console.print(
            f"[yellow]Injecting {self.config['fault']} fault on "
            f"{container_name}:{interface} for {duration}s "
            f"({' '.join(netem_args)})...[/yellow]"
        )

        # Always try to clear any leftover qdisc first so reruns are idempotent.
        # Ignore non-zero exit codes — a missing qdisc is the common case.
        container.exec_run(
            ["tc", "qdisc", "del", "dev", interface, "root"],
        )

        add_cmd = [
            "tc",
            "qdisc",
            "add",
            "dev",
            interface,
            "root",
            "netem",
            *netem_args,
        ]
        add_result = container.exec_run(add_cmd)
        if add_result.exit_code != 0:
            # `exec_run` without demux=True returns combined stdout+stderr.
            # Naming it `output` keeps that honest for future maintainers.
            output = add_result.output.decode("utf-8", errors="replace")
            hint = ""
            if "executable file not found" in output or "tc: not found" in output:
                hint = (
                    " — the container image does not ship `tc` (iproute2). "
                    "The stock merod image is one of these; build a thin "
                    "image on top with `apt-get install -y iproute2` and "
                    "point `nodes.image` at it."
                )
            elif "Operation not permitted" in output or "EPERM" in output:
                hint = (
                    " — looks like NET_ADMIN is missing. Ensure the workflow's "
                    "`nodes:` block does not set `network_admin: false`."
                )
            safe_console_error(
                "✗ Failed to apply netem on {container}: {err}{hint}",
                container=container_name,
                err=output.strip() or "unknown error",
                hint=hint,
            )
            return False

        await asyncio.sleep(duration)

        del_result = container.exec_run(
            ["tc", "qdisc", "del", "dev", interface, "root"],
        )
        if del_result.exit_code != 0:
            # The fault duration has already elapsed, so the user-visible
            # fault window is done; failing the step here would abort the
            # workflow without un-stucking the qdisc anyway. Surface a loud
            # error with the remediation and let the workflow continue —
            # the next inject_network_fault auto-clears via the leading
            # `tc qdisc del`, and restart_container is the manual escape.
            output = del_result.output.decode("utf-8", errors="replace")
            safe_console_error(
                "✗ Failed to clear netem on {container} ({err}). Stale qdisc "
                "remains on the container — subsequent steps may see degraded "
                "network. Use restart_container to clear it.",
                container=container_name,
                err=output.strip() or "unknown",
            )
            return True

        console.print(f"[green]✓ Cleared fault on {container_name}[/green]")
        return True
