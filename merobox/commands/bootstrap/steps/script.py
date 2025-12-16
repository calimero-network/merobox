"""
Script execution step for bootstrap workflow.
"""

import io
import os
import subprocess
import tarfile
import time
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class ScriptStep(BaseStep):
    """Execute a script on Docker images or running nodes."""

    def __init__(self, config: dict[str, Any], manager: object | None = None):
        super().__init__(config, manager)
        self.script_path = config.get("script")
        self.target = config.get("target", "image")  # 'image' or 'nodes'
        self.description = config.get(
            "description", f"Execute script: {self.script_path}"
        )
        # Optional script arguments (list of strings)
        self.script_args = config.get("args", [])

    def _resolve_script_args(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> list[str]:
        """Resolve placeholders in script arguments using BaseStep resolver."""
        resolved: list[str] = []
        for arg in self.script_args:
            if isinstance(arg, str):
                resolved.append(
                    self._resolve_dynamic_value(arg, workflow_results, dynamic_values)
                )
        return resolved

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["script"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate script is a string
        if not isinstance(self.config.get("script"), str):
            raise ValueError(f"Step '{step_name}': 'script' must be a string")

        # Validate target is a string if provided
        if "target" in self.config and not isinstance(self.config["target"], str):
            raise ValueError(f"Step '{step_name}': 'target' must be a string")

        # Validate target value is valid if provided
        if "target" in self.config and self.config["target"] not in [
            "image",
            "nodes",
            "local",
        ]:
            raise ValueError(
                f"Step '{step_name}': 'target' must be one of 'image', 'nodes', or 'local'"
            )

        # Validate description is a string if provided
        if "description" in self.config and not isinstance(
            self.config["description"], str
        ):
            raise ValueError(f"Step '{step_name}': 'description' must be a string")

        # Validate args is a list of strings if provided
        if "args" in self.config:
            args_val = self.config["args"]
            if not isinstance(args_val, list) or not all(
                isinstance(a, str) for a in args_val
            ):
                raise ValueError(
                    f"Step '{step_name}': 'args' must be a list of strings"
                )

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from script execution:
        - exit_code: Script exit code
        - output: Script output/result
        - execution_time: Time taken to execute the script
        - node_name: Name of the node where script was executed
        - script_path: Path to the executed script
        - env_vars: Environment variables set by the script (if any)
        """
        return [
            ("exit_code", "script_exit_code_{node_name}", "Script exit code"),
            ("output", "script_output_{node_name}", "Script output/result"),
            (
                "execution_time",
                "script_execution_time_{node_name}",
                "Time taken to execute the script",
            ),
            ("script_path", "script_path_{node_name}", "Path to the executed script"),
            (
                "env_vars",
                "script_env_vars_{node_name}",
                "Environment variables set by the script",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """Execute the script step."""
        if not self.script_path:
            console.print("[red]❌ Script path not specified[/red]")
            return False

        if not os.path.exists(self.script_path):
            console.print(f"[red]❌ Script file not found: {self.script_path}[/red]")
            return False

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Script step export configuration validation failed[/yellow]"
            )

        console.print(f"\n[bold blue]📜 {self.description}[/bold blue]")

        # Resolve script args now so all execution targets use the same values
        resolved_args = self._resolve_script_args(workflow_results, dynamic_values)

        if self.target == "image":
            return await self._execute_on_image(
                workflow_results, dynamic_values, resolved_args
            )
        elif self.target == "nodes":
            return await self._execute_on_nodes(
                workflow_results, dynamic_values, resolved_args
            )
        elif self.target == "local":
            return await self._execute_local(
                workflow_results, dynamic_values, resolved_args
            )
        else:
            console.print(f"[red]❌ Unknown target type: {self.target}[/red]")
            return False

    async def _execute_local(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        resolved_args: list[str],
    ) -> bool:
        """Execute script locally on the host machine."""
        try:
            console.print(
                f"[yellow]Executing script locally: {self.script_path}[/yellow]"
            )

            if not os.path.exists(self.script_path):
                console.print(
                    f"[red]❌ Script file not found: {self.script_path}[/red]"
                )
                return False

            # Ensure script is readable
            try:
                with open(self.script_path) as file:
                    file.read(1)
            except Exception as e:
                console.print(f"[red]Failed to read script file: {str(e)}[/red]")
                return False

            # Prepare environment variables from dynamic_values
            # This allows scripts to access workflow variables via $VAR_NAME
            env = os.environ.copy()

            # Add dynamic values as environment variables (with uppercase conversion)
            for key, value in dynamic_values.items():
                # Convert to uppercase and replace special chars with underscores
                env_key = key.upper().replace("-", "_").replace(".", "_")
                env[env_key] = str(value) if value is not None else ""

            # Run the script using /bin/sh
            start_time = time.time()
            try:
                completed = subprocess.run(
                    ["/bin/sh", self.script_path, *resolved_args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    env=env,
                )
            except Exception as e:
                console.print(f"[red]Failed to execute local script: {str(e)}[/red]")
                return False

            execution_time = time.time() - start_time
            output = completed.stdout or ""

            if output.strip():
                console.print("[cyan]Local script output:[/cyan]")
                console.print(output)

            if completed.returncode != 0:
                console.print(
                    f"[red]Local script failed with exit code: {completed.returncode}[/red]"
                )
                # Still export results for diagnostics
                self._export_script_results(
                    "local",
                    completed.returncode,
                    output,
                    execution_time,
                    dynamic_values,
                )
                return False

            # Export results
            self._export_script_results(
                "local", completed.returncode, output, execution_time, dynamic_values
            )
            console.print("[green]✓ Local script executed successfully[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Failed to execute local script: {str(e)}[/red]")
            return False

    def _export_script_results(
        self,
        node_name: str,
        exit_code: int,
        output: str,
        execution_time: float,
        dynamic_values: dict[str, Any],
    ) -> None:
        """Export script execution results to dynamic values."""
        script_results = {
            "exit_code": exit_code,
            "output": output,
            "execution_time": execution_time,
            "script_path": self.script_path,
            "node_name": node_name,
        }

        # Export individual variables
        for key, value in script_results.items():
            if key == "node_name":
                continue  # Skip node_name as it's used in the template
            target_key = f"script_{key}_{node_name}"
            dynamic_values[target_key] = value
            console.print(
                f"[blue]📝 Exported script {key} → {target_key}: {value}[/blue]"
            )

        # Export environment variables if any were set
        env_vars = {}
        common_env_vars = [
            "NODE_READY",
            "NODE_HOSTNAME",
            "NODE_TIMESTAMP",
            "CALIMERO_HOME",
            "TOOLS_INSTALLED",
            "CURL_AVAILABLE",
            "PERF_AVAILABLE",
            "PACKAGE_MANAGER",
            "UPDATE_CMD",
            "INSTALL_CMD",
        ]

        for var_name in common_env_vars:
            if var_name in os.environ:
                env_vars[var_name] = os.environ[var_name]

        if env_vars:
            dynamic_values[f"script_env_vars_{node_name}"] = env_vars
            console.print(
                f"[blue]📝 Exported {len(env_vars)} environment variables for {node_name}[/blue]"
            )
            for var_name, var_value in env_vars.items():
                console.print(f"  {var_name}={var_value}")

    async def _execute_on_image(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        resolved_args: list[str],
    ) -> bool:
        """Execute script on a Docker image before starting nodes."""
        try:
            console.print(
                f"[yellow]Executing script on Docker image: {self.script_path}[/yellow]"
            )

            # Read the script content
            try:
                with open(self.script_path) as file:
                    file.read()  # Read file to validate it exists
            except Exception as e:
                console.print(f"[red]Failed to read script file: {str(e)}[/red]")
                return False

            # Get the base image from the workflow context
            # We'll use a default image since we don't have direct access to the config here
            image = "ghcr.io/calimero-network/merod:edge"

            console.print(f"[cyan]Using Docker image: {image}[/cyan]")

            # Create a temporary container to execute the script
            temp_container_name = f"script-step-{int(time.time())}"

            try:
                if self.manager is not None and hasattr(self.manager, "binary_path"):
                    console.print(
                        "[yellow]Skipping image script execution: running in --no-docker mode (no container runtime available)[/yellow]"
                    )
                    return True

                # Create container with the script mounted
                try:
                    if self.manager is not None:
                        manager = self.manager
                    else:
                        from merobox.commands.manager import DockerManager

                        manager = DockerManager()

                    # Ensure the image is available
                    if not manager._ensure_image_pulled(image):
                        console.print(
                            f"[red]✗ Cannot proceed without image: {image}[/red]"
                        )
                        return False

                    container = manager.client.containers.run(
                        name=temp_container_name,
                        image=image,
                        detach=True,
                        entrypoint="",  # Override the merod entrypoint
                        command=[
                            "sh",
                            "-c",
                            "while true; do sleep 1; done",
                        ],  # Keep container running
                        volumes={
                            os.path.abspath(self.script_path): {
                                "bind": "/tmp/script.sh",
                                "mode": "ro",
                            }
                        },
                        working_dir="/tmp",
                    )
                except Exception as create_error:
                    console.print(
                        f"[red]Failed to create container: {str(create_error)}[/red]"
                    )
                    return False

                # Wait for container to be ready
                time.sleep(2)
                container.reload()

                if container.status != "running":
                    console.print(
                        "[red]Failed to start temporary container for script[/red]"
                    )
                    console.print(f"[red]Container status: {container.status}[/red]")
                    try:
                        logs = container.logs().decode("utf-8")
                        if logs.strip():
                            console.print(f"[red]Container logs: {logs}[/red]")
                    except Exception:
                        pass
                    try:
                        container.remove()
                    except Exception:
                        pass
                    return False

                # Make script executable and run it
                console.print("[cyan]Running script in container...[/cyan]")

                result = container.exec_run(["chmod", "+x", "/tmp/script.sh"])
                if result.exit_code != 0:
                    console.print(
                        f"[yellow]Warning: Could not make script executable: {result.output.decode()}[/yellow]"
                    )

                start_time = time.time()
                cmd = ["/bin/sh", "/tmp/script.sh", *resolved_args]
                result = container.exec_run(cmd)
                execution_time = time.time() - start_time

                output = result.output.decode("utf-8", errors="replace")
                if output.strip():
                    console.print("[cyan]Script output:[/cyan]")
                    console.print(output)

                if result.exit_code != 0:
                    console.print(
                        f"[red]Script failed with exit code: {result.exit_code}[/red]"
                    )
                    return False

                # Export script results
                self._export_script_results(
                    "image", result.exit_code, output, execution_time, dynamic_values
                )

                console.print("[green]✓ Script executed successfully[/green]")
                return True

            finally:
                try:
                    container.stop(timeout=5)
                    container.remove()
                except Exception as e:
                    console.print(
                        f"[yellow]Warning: Could not clean up temporary container: {str(e)}[/yellow]"
                    )

        except Exception as e:
            console.print(f"[red]Failed to execute script: {str(e)}[/red]")
            return False

    async def _execute_on_nodes(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        resolved_args: list[str],
    ) -> bool:
        """Execute script on all running Calimero nodes."""
        try:
            console.print(
                f"[yellow]Executing script on all running nodes: {self.script_path}[/yellow]"
            )

            # Read the script content
            try:
                with open(self.script_path) as file:
                    script_content = file.read()
            except Exception as e:
                console.print(f"[red]Failed to read script file: {str(e)}[/red]")
                return False

            # Get all running Calimero nodes

            if self.manager is not None and hasattr(self.manager, "binary_path"):
                console.print(
                    "[yellow]Skipping node script execution: running in --no-docker mode (node exec not implemented for binaries)[/yellow]"
                )
                return True

            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            containers = manager.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
                return True

            console.print(
                f"[cyan]Found {len(containers)} running nodes to execute script on[/cyan]"
            )

            success_count = 0
            failed_nodes = []

            for container in containers:
                node_name = container.name
                console.print(f"\n[cyan]Executing script on {node_name}...[/cyan]")

                try:
                    # Copy the script to the container
                    script_name = f"script_{int(time.time())}.sh"

                    # Create a temporary tar archive with the script
                    tar_buffer = io.BytesIO()
                    with tarfile.open(fileobj=tar_buffer, mode="w:tar") as tar:
                        # Create tarinfo for the script
                        tarinfo = tarfile.TarInfo(script_name)
                        tarinfo.size = len(script_content.encode("utf-8"))
                        tarinfo.mode = 0o755  # Executable permissions

                        # Add the script to the tar archive
                        tar.addfile(tarinfo, io.BytesIO(script_content.encode("utf-8")))

                    # Get the tar archive bytes
                    tar_data = tar_buffer.getvalue()

                    try:
                        # Copy script to container using put_archive
                        container.put_archive("/tmp/", tar_data)

                        # Make script executable
                        result = container.exec_run(
                            ["chmod", "+x", f"/tmp/{script_name}"]
                        )
                        if result.exit_code != 0:
                            console.print(
                                f"[yellow]Warning: Could not make script executable on {node_name}: {result.output.decode()}[/yellow]"
                            )

                        # Execute the script
                        start_time = time.time()
                        cmd = ["/bin/sh", f"/tmp/{script_name}", *resolved_args]
                        result = container.exec_run(cmd)
                        execution_time = time.time() - start_time

                        # Display script output
                        output = result.output.decode("utf-8", errors="replace")
                        if output.strip():
                            console.print(
                                f"[cyan]Script output from {node_name}:[/cyan]"
                            )
                            console.print(output)

                        # Check exit code
                        if result.exit_code != 0:
                            console.print(
                                f"[red]Script failed on {node_name} with exit code: {result.exit_code}[/red]"
                            )
                            failed_nodes.append(node_name)
                        else:
                            console.print(
                                f"[green]✓ Script executed successfully on {node_name}[/green]"
                            )
                            success_count += 1

                        # Export script results for this node
                        self._export_script_results(
                            node_name,
                            result.exit_code,
                            output,
                            execution_time,
                            dynamic_values,
                        )

                        # Clean up script from container
                        try:
                            container.exec_run(["rm", f"/tmp/{script_name}"])
                        except Exception:
                            pass

                    finally:
                        # Clean up tar buffer
                        tar_buffer.close()

                except Exception as e:
                    console.print(
                        f"[red]Failed to execute script on {node_name}: {str(e)}[/red]"
                    )
                    failed_nodes.append(node_name)

            # Summary
            console.print(
                f"\n[bold]Script execution summary: {success_count}/{len(containers)} nodes successful[/bold]"
            )

            if failed_nodes:
                console.print(f"[red]Failed on nodes: {', '.join(failed_nodes)}[/red]")
                return False

            console.print("[green]✓ Script executed successfully on all nodes[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Failed to execute script on nodes: {str(e)}[/red]")
            return False
