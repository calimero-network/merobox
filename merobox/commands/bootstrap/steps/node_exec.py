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
        for field in ("image", "data_dir"):
            value = self.config.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
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

        Reading both off the container is the most accurate source and the first
        thing tried — but it cannot be the only one: `stop_node` **removes** the
        container (`_graceful_stop_containers_batch` stops *and* removes), so by
        the time an offline command runs there is usually nothing left to inspect.
        That is not an edge case, it is the main path: the whole reason this step
        exists is to run against a node that has been stopped.

        So, in order:

        1. the container, if it still exists (a running node under
           `allow_running`, or a flow that stopped it another way);
        2. the step's explicit `image:` / `data_dir:`, for anything the
           conventions cannot cover;
        3. what merobox itself knows — the image the manager recorded when it
           started the node, and its `./data/<node>` bind-mount convention.
        """
        image: Optional[str] = None
        source: Optional[str] = None

        try:
            container = self.manager.client.containers.get(node_name)
            image = container.attrs.get("Config", {}).get("Image")
            for mount in container.attrs.get("Mounts", []):
                if mount.get("Destination") == CONTAINER_HOME:
                    source = mount.get("Source")
                    break
        except Exception:  # noqa: BLE001 - absence is expected, not exceptional
            pass

        image = (
            image
            or self.config.get("image")
            or getattr(self.manager, "node_images", {}).get(node_name)
        )
        if not image:
            raise RuntimeError(
                f"could not determine which image to run for {node_name}: its "
                "container is gone and merobox has no record of starting it. Pass "
                "`image:` on the step."
            )

        if not source:
            source = self.config.get("data_dir")
        if not source:
            # `<data_dir>/<node>/config.toml`, so the directory bound to
            # /app/data is that path's grandparent. Exact, unlike rebuilding a
            # relative path — the manager keeps this record precisely because
            # reconstruction "would break if the CWD changed, or if a custom
            # data_dir was used".
            config_file = getattr(self.manager, "node_config_files", {}).get(node_name)
            if config_file:
                source = os.path.dirname(os.path.dirname(config_file))
        if not source:
            source = os.path.abspath(os.path.join("data", node_name))

        # Check for the node's HOME, not just the directory: `--home /app/data
        # --node <name>` reads `<source>/<name>/config.toml`, so an existing but
        # wrong `source` passed an isdir() check and failed later inside merod
        # with "Node is not initialized" — naming a path the log never showed.
        node_home = os.path.join(source, node_name)
        if not os.path.isdir(node_home):
            raise RuntimeError(
                f"'{source}' does not hold {node_name}'s home (expected "
                f"'{node_home}'), so `--home {CONTAINER_HOME} --node {node_name}` "
                "would find nothing. Pass `data_dir:` if it lives elsewhere."
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

            # `merod` explicitly with the entrypoint cleared, which is how merobox
            # starts node containers itself (`run_node` sets `entrypoint = ""` and
            # passes "merod" as argv[0]). Relying on the image's ENTRYPOINT works
            # for the images that have one and silently does the wrong thing for
            # any that wrap it — matching the pattern that already works here is
            # cheaper than depending on every image agreeing with us.
            command = ["merod", "--home", CONTAINER_HOME, "--node", node_name, *args]
            console.print(
                f"[cyan]node_exec[/cyan] {node_name}: merod {' '.join(args)}\n"
                f"  image: {image}\n"
                f"  mount: {host_home} -> {CONTAINER_HOME}"
            )

            container = self.manager.client.containers.create(
                image=image,
                entrypoint="",
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
            result = fail(f"node_exec failed: {e}", error=e)

        expected_failure = bool(self.config.get("expected_failure", False))

        if not result["success"]:
            # `result["error"]` is the summary; the exception carries the reason
            # (a missing container, a non-zero exit with its stderr). Printing only
            # the summary made a CI failure read "node_exec failed: node_exec
            # failed", which is how this step's first real bug had to be diagnosed
            # by inference instead of by reading the log.
            detail = result.get("details") or result.get("error")
            if expected_failure:
                console.print(
                    f"[green]✓[/green] node_exec on {node_name} failed as expected: "
                    f"{detail}"
                )
                return True
            console.print(f"[red]node_exec on {node_name} failed: {detail}[/red]")
            return False

        if expected_failure:
            console.print(
                f"[red]node_exec on {node_name} succeeded, but the step expected it "
                "to fail[/red]"
            )
            return False

        console.print(f"[green]✓[/green] node_exec on {node_name} succeeded")
        workflow_results[f"exec_{node_name}"] = result["data"]
        # As above: recording is not exporting. `outputs: { phrase:
        # stdout_first_line }` is inert unless this runs.
        self._export_variables(result["data"], node_name, dynamic_values)
        return True
