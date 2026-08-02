"""
Run an offline `merod` subcommand against a stopped node's data directory.

Some node operations are CLI-only and deliberately cannot run against a live
node: `merod account export|import` opens the datastore directly, and RocksDB
holds an exclusive lock while `merod run` is up. That rules out both obvious
approaches — `docker exec` needs a *running* container, and the admin API does
not expose the recovery key (serving it over HTTP would be the wrong shape for a
secret whose whole point is to live offline).

What does work: the node's data directory is a host bind mount, and the image's
entrypoint is `merod` itself. So a one-shot container over the same directory can
run any subcommand while the node is down.

This is one general step rather than an `account_export` / `account_import` pair
because the awkward part is not the account plane — it is "invoke the binary
offline", which is equally what a config edit or a future migration tool needs. A
step per subcommand would re-solve the container plumbing each time and would
have to ship in lockstep with core's CLI surface; this ships once.

Image and mount are read off the existing container rather than reconstructed
from workflow config: a stopped container still reports both, so the step cannot
disagree with how the node was actually started.
"""

import os
from typing import Any, Optional

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

#: Where merobox mounts a node's home inside the container.
CONTAINER_HOME = "/app/data"

#: Offline commands are local disk work; generous, but bounded so a wedged
#: invocation fails the step instead of hanging the scenario.
NODE_EXEC_TIMEOUT = 120


class NodeExecStep(BaseStep):
    """Run `merod --home … --node <name> <args…>` in a one-shot container.

    The node must be **stopped**: the datastore is opened directly and RocksDB's
    lock is exclusive. Running against a live node is refused rather than
    attempted, because the failure otherwise surfaces as an opaque lock error
    several layers down.

    Exports `stdout`, `stdout_first_line` (what a single-value command like
    `account export` actually produces, ahead of its advisory output), `stderr`
    and `exit_code`.

    `expected_failure: true` inverts the outcome, so a scenario can assert that a
    guard refuses — `account import` without `--force` against an existing root,
    say. Implemented here rather than inherited: `expected_failure` is a per-step
    contract in this codebase, and a step that ignored it would let a scenario
    claim to test a refusal while testing nothing.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "args"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        args = self.config.get("args")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"Step '{step_name}': 'args' must be a list of strings")
        files = self.config.get("files")
        if files is not None and not isinstance(files, dict):
            raise ValueError(
                f"Step '{step_name}': 'files' must be a mapping of "
                "container path -> contents"
            )
        for flag in ("allow_running", "expected_failure"):
            value = self.config.get(flag)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"Step '{step_name}': '{flag}' must be a bool")

    def _get_exportable_variables(self):
        return [
            ("stdout", "exec_stdout_{node_name}", "Everything the command printed"),
            (
                "stdout_first_line",
                "exec_first_line_{node_name}",
                "First line of stdout — the value a single-value command emits",
            ),
            ("stderr", "exec_stderr_{node_name}", "Anything on stderr"),
            ("exit_code", "exec_exit_code_{node_name}", "The command's exit status"),
        ]

    def _container_spec(self, node_name: str) -> tuple[str, str]:
        """The node's image and the HOST path backing its `/app/data` mount.

        Read from the container itself so this cannot drift from how the node was
        started. Works while stopped, which is the only state this step runs in.
        """
        container = self.manager.client.containers.get(node_name)
        image = container.attrs.get("Config", {}).get("Image")
        if not image:
            raise RuntimeError(f"could not determine the image for {node_name}")

        source: Optional[str] = None
        for mount in container.attrs.get("Mounts", []):
            if mount.get("Destination") == CONTAINER_HOME:
                source = mount.get("Source")
                break
        if not source:
            raise RuntimeError(
                f"{node_name} has no bind mount at {CONTAINER_HOME}, so there is "
                "no data directory to run against"
            )
        return image, source

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = str(
            self._resolve_dynamic_value(self.config["node"], {}, dynamic_values)
        )
        args = [
            str(self._resolve_dynamic_value(a, {}, dynamic_values))
            for a in self.config["args"]
        ]
        files = {
            path: str(self._resolve_dynamic_value(content, {}, dynamic_values))
            for path, content in (self.config.get("files") or {}).items()
        }
        allow_running = bool(self.config.get("allow_running", False))

        if self.manager is None or not hasattr(self.manager, "client"):
            console.print(
                "[red]node_exec needs a local Docker-managed node; it cannot run "
                "against a remote node.[/red]"
            )
            return False

        try:
            if not allow_running and self.manager.is_node_running(node_name):
                raise RuntimeError(
                    f"{node_name} is running. An offline command opens the "
                    "datastore directly and RocksDB's lock is exclusive — stop the "
                    "node first (`type: stop_node`), or set allow_running: true if "
                    "the command genuinely does not touch the store."
                )

            image, host_home = self._container_spec(node_name)

            # Input files are written on the HOST side of the bind mount, so the
            # one-shot container sees them at their container path. This is how a
            # command that reads a file (`account import --from …`) gets its input
            # without stdin plumbing through the Docker API.
            for container_path, content in files.items():
                if not container_path.startswith(f"{CONTAINER_HOME}/"):
                    raise RuntimeError(
                        f"'{container_path}' is outside {CONTAINER_HOME}, so the "
                        "container would not see it"
                    )
                relative = container_path[len(CONTAINER_HOME) + 1 :]
                host_path = os.path.join(host_home, relative)
                os.makedirs(os.path.dirname(host_path), exist_ok=True)
                with open(host_path, "w", encoding="utf-8") as handle:
                    handle.write(content if content.endswith("\n") else content + "\n")

            command = ["--home", CONTAINER_HOME, "--node", node_name, *args]
            console.print(f"[cyan]node_exec[/cyan] {node_name}: merod {' '.join(args)}")

            container = self.manager.client.containers.create(
                image=image,
                command=command,
                volumes={host_home: {"bind": CONTAINER_HOME, "mode": "rw"}},
                environment={"CALIMERO_HOME": CONTAINER_HOME},
            )
            try:
                container.start()
                status = container.wait(timeout=NODE_EXEC_TIMEOUT)
                exit_code = status.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode(
                    "utf-8", errors="replace"
                )
                stderr = container.logs(stdout=False, stderr=True).decode(
                    "utf-8", errors="replace"
                )
            finally:
                container.remove(force=True)

            if exit_code != 0:
                raise RuntimeError(
                    f"merod {' '.join(args)} exited {exit_code}\n"
                    f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
                )

            first_line = next(
                (line for line in stdout.splitlines() if line.strip()), ""
            )
            result = ok(
                {
                    "stdout": stdout,
                    "stdout_first_line": first_line.strip(),
                    "stderr": stderr,
                    "exit_code": exit_code,
                }
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("node_exec failed", error=e)

        expected_failure = bool(self.config.get("expected_failure", False))

        if not result["success"]:
            if expected_failure:
                console.print(
                    f"[green]✓[/green] node_exec on {node_name} failed as expected: "
                    f"{result.get('error')}"
                )
                return True
            console.print(
                f"[red]node_exec on {node_name} failed: {result.get('error')}[/red]"
            )
            return False

        if expected_failure:
            console.print(
                f"[red]node_exec on {node_name} succeeded, but the step expected it "
                "to fail[/red]"
            )
            return False

        console.print(f"[green]✓[/green] node_exec on {node_name} succeeded")
        workflow_results[f"exec_{node_name}"] = result["data"]
        return True
