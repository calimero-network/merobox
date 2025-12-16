"""
Binary Manager - Manages Calimero nodes as native processes (no Docker).
"""

import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import toml
from rich.console import Console

from merobox.commands.config_utils import apply_near_devnet_config_to_file

console = Console()


class BinaryManager:
    """Manages Calimero nodes as native binary processes."""

    def __init__(self, binary_path: Optional[str] = None, require_binary: bool = True):
        """
        Initialize the BinaryManager.

        Args:
            binary_path: Path to the merod binary. If None, searches PATH.
            require_binary: If True, exit if binary not found. If False, set to None gracefully.
        """
        if binary_path:
            self.binary_path = binary_path
        else:
            self.binary_path = self._find_binary(require=require_binary)

        self.processes = {}  # node_name -> subprocess.Popen
        self.node_rpc_ports: dict[str, int] = {}
        self.pid_file_dir = Path("./data/.pids")
        self.pid_file_dir.mkdir(parents=True, exist_ok=True)

    def _find_binary(self, require: bool = True) -> Optional[str]:
        """Find the merod binary in PATH or common locations.

        Args:
            require: If True, exit if not found. If False, return None gracefully.
        """
        # Check PATH
        from shutil import which

        binary = which("merod")
        if binary:
            console.print(f"[green]✓ Found merod binary in PATH: {binary}[/green]")
            return binary

        # Check common locations
        common_paths = [
            "/usr/local/bin/merod",
            "/usr/bin/merod",
            os.path.expanduser("~/bin/merod"),
            "./merod",
            "../merod",
        ]

        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                console.print(f"[green]✓ Found merod binary: {path}[/green]")
                return path

        # Not found - either exit or return None
        if require:
            console.print(
                "[red]✗ merod binary not found. Please install or specify --binary-path[/red]"
            )
            console.print(
                "[yellow]Searched: PATH and common locations (/usr/local/bin, /usr/bin, ~/bin, ./)[/yellow]"
            )
            console.print("\n[yellow]Install via Homebrew (macOS):[/yellow]")
            console.print("  brew tap calimero-network/homebrew-tap")
            console.print("  brew install merod")
            console.print("  merod --version")
            sys.exit(1)
        else:
            return None

    def _get_pid_file(self, node_name: str) -> Path:
        """Get the PID file path for a node."""
        return self.pid_file_dir / f"{node_name}.pid"

    def _save_pid(self, node_name: str, pid: int):
        """Save process PID to file."""
        pid_file = self._get_pid_file(node_name)
        pid_file.write_text(str(pid))

    def _load_pid(self, node_name: str) -> Optional[int]:
        """Load process PID from file."""
        pid_file = self._get_pid_file(node_name)
        if pid_file.exists():
            try:
                return int(pid_file.read_text().strip())
            except (ValueError, OSError):
                return None
        return None

    def _remove_pid_file(self, node_name: str):
        """Remove PID file."""
        pid_file = self._get_pid_file(node_name)
        if pid_file.exists():
            pid_file.unlink()

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running."""
        try:
            os.kill(pid, 0)  # Signal 0 checks if process exists
            return True
        except (OSError, ProcessLookupError):
            return False

    def run_node(
        self,
        node_name: str,
        port: int = 2428,
        rpc_port: int = 2528,
        chain_id: str = "testnet-1",
        data_dir: Optional[str] = None,
        image: Optional[str] = None,  # Ignored in binary mode
        auth_service: bool = False,  # Ignored in binary mode
        auth_image: Optional[str] = None,  # Ignored in binary mode
        auth_use_cached: bool = False,  # Ignored in binary mode
        webui_use_cached: bool = False,  # Ignored in binary mode
        log_level: str = "debug",
        rust_backtrace: str = "0",
        foreground: bool = False,
        mock_relayer: bool = False,  # Ignored in binary mode
        workflow_id: Optional[str] = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        config_path: Optional[str] = None,  # custom config.toml path
        near_devnet_config: dict = None,  # Enable NEAR Devnet
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
    ) -> bool:
        """
        Run a Calimero node as a native binary process.

        Args:
            node_name: Name of the node
            port: P2P port
            rpc_port: RPC port
            chain_id: Chain ID
            data_dir: Data directory (defaults to ./data/{node_name})
            log_level: Rust log level
            rust_backtrace: RUST_BACKTRACE level

        Returns:
            True if successful, False otherwise
        """
        try:
            if mock_relayer:
                console.print(
                    "[yellow]⚠ Mock relayer is not supported in binary mode (--no-docker); flag will be ignored[/yellow]"
                )
            # Default ports if None provided
            if port is None:
                port = 2428
            if rpc_port is None:
                rpc_port = 2528

            # Check if node is already running
            existing_pid = self._load_pid(node_name)
            if existing_pid and self._is_process_running(existing_pid):
                console.print(
                    f"[yellow]Node {node_name} is already running (PID: {existing_pid})[/yellow]"
                )
                console.print("[yellow]Stopping existing process...[/yellow]")
                self.stop_node(node_name)

            # Prepare data directory
            if data_dir is None:
                data_dir = f"./data/{node_name}"

            data_path = Path(data_dir)
            data_path.mkdir(parents=True, exist_ok=True)

            # Create node-specific subdirectory
            node_data_dir = data_path / node_name
            node_data_dir.mkdir(parents=True, exist_ok=True)

            # Handle custom config if provided
            skip_init = False
            if config_path is not None:
                config_source = Path(config_path)
                if not config_source.exists():
                    console.print(
                        f"[red]✗ Custom config file not found: {config_path}[/red]"
                    )
                    return False

                config_dest_dir = node_data_dir / node_name
                config_dest_dir.mkdir(parents=True, exist_ok=True)
                config_dest = config_dest_dir / "config.toml"
                try:
                    shutil.copy2(config_source, config_dest)
                    console.print(
                        f"[green]✓ Copied custom config from {config_path} to {config_dest}[/green]"
                    )
                    skip_init = True
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to copy custom config: {str(e)}[/red]"
                    )
                    return False

            # Prepare log file (not used when foreground)
            log_dir = data_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"{node_name}.log"

            console.print(f"[cyan]Starting node {node_name}...[/cyan]")
            console.print(f"[cyan]  Binary: {self.binary_path}[/cyan]")
            console.print(f"[cyan]  Data dir: {node_data_dir}[/cyan]")
            console.print(f"[cyan]  P2P port: {port}[/cyan]")
            console.print(f"[cyan]  RPC port: {rpc_port}[/cyan]")
            console.print(f"[cyan]  Log file: {log_file}[/cyan]")

            # Prepare environment
            env = os.environ.copy()
            env["CALIMERO_HOME"] = str(node_data_dir.absolute())
            env["NODE_NAME"] = node_name
            env["RUST_LOG"] = log_level
            env["RUST_BACKTRACE"] = rust_backtrace

            # Initialize node if needed (unless using custom config)
            if not skip_init:
                config_file = node_data_dir / "config.toml"
                if not config_file.exists():
                    console.print(
                        f"[yellow]Initializing node {node_name} (first run)...[/yellow]"
                    )
                    init_cmd = [
                        self.binary_path,
                        "--home",
                        str(node_data_dir.absolute()),
                        "--node-name",
                        node_name,
                        "init",
                        "--server-port",
                        str(rpc_port),
                        "--swarm-port",
                        str(port),
                    ]
                    with open(log_file, "a") as log_f:
                        try:
                            subprocess.run(
                                init_cmd,
                                check=True,
                                env=env,
                                stdout=log_f,
                                stderr=subprocess.STDOUT,
                            )
                            console.print(
                                f"[green]✓ Node {node_name} initialized successfully[/green]"
                            )
                        except subprocess.CalledProcessError as e:
                            console.print(
                                f"[red]✗ Failed to initialize node {node_name}: {e}[/red]"
                            )
                            console.print(f"[yellow]Check logs: {log_file}[/yellow]")
                            return False
            else:
                console.print(
                    f"[cyan]Skipping initialization for {node_name} (using custom config)[/cyan]"
                )

            # The actual config file is in a nested subdirectory created by merod init
            actual_config_file = node_data_dir / node_name / "config.toml"

            # Apply e2e-style configuration for reliable testing (only if e2e_mode is enabled)
            if e2e_mode:
                self._apply_e2e_defaults(actual_config_file, node_name, workflow_id)

            # Apply bootstrap nodes configuration (works regardless of e2e_mode)
            if bootstrap_nodes:
                self._apply_bootstrap_nodes(
                    actual_config_file, node_name, bootstrap_nodes
                )

            # Apply NEAR Devnet config if provided
            if near_devnet_config:
                console.print(
                    "[green]✓ Applying Near Devnet config for the node [/green]"
                )

                actual_config_file = node_data_dir / node_name / "config.toml"
                if not self._apply_near_devnet_config(
                    actual_config_file,
                    node_name,
                    near_devnet_config["rpc_url"],
                    near_devnet_config["contract_id"],
                    near_devnet_config["account_id"],
                    near_devnet_config["public_key"],
                    near_devnet_config["secret_key"],
                ):
                    console.print("[red]✗ Failed to apply NEAR Devnet config[/red]")
                    return False

            # Build run command (ports are taken from config created during init)
            cmd = [
                self.binary_path,
                "--home",
                str(node_data_dir.absolute()),
                "--node-name",
                node_name,
                "run",
            ]

            if foreground:
                # Start attached in foreground (inherit stdio)
                try:
                    process = subprocess.Popen(
                        cmd,
                        env=env,
                    )
                    self.processes[node_name] = process
                    self._save_pid(node_name, process.pid)
                    try:
                        self.node_rpc_ports[node_name] = int(rpc_port)
                    except (TypeError, ValueError):
                        pass
                    console.print(
                        f"[green]✓ Node {node_name} started (foreground) (PID: {process.pid})[/green]"
                    )
                    # Wait until process exits
                    process.wait()
                    # Cleanup pid file on exit
                    self._remove_pid_file(node_name)
                    return True
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to start node {node_name}: {str(e)}[/red]"
                    )
                    return False
            else:
                # Start detached with logs to file
                # For e2e mode, don't create new session to match e2e test behavior
                # (process should be managed together with parent, not detached)
                # For regular mode, create new session so process survives parent death
                with open(log_file, "a") as log_f:
                    popen_kwargs = {
                        "env": env,
                        "stdin": subprocess.DEVNULL,
                        "stdout": log_f,
                        "stderr": subprocess.STDOUT,
                    }
                    # Only create new session if NOT in e2e mode
                    # E2E tests work better when process is in same process group
                    if not e2e_mode:
                        popen_kwargs["start_new_session"] = True

                    process = subprocess.Popen(cmd, **popen_kwargs)

                # Save process info
                self.processes[node_name] = process
                self._save_pid(node_name, process.pid)
                try:
                    self.node_rpc_ports[node_name] = int(rpc_port)
                except (TypeError, ValueError):
                    pass

                console.print(
                    f"[green]✓ Node {node_name} started successfully (PID: {process.pid})[/green]"
                )
                console.print(f"[cyan]  View logs: tail -f {log_file}[/cyan]")
                console.print(
                    f"[cyan]  Admin Dashboard: http://localhost:{rpc_port}/admin-dashboard[/cyan]"
                )

                # Wait a moment to check if process stays alive
                time.sleep(2)
                if not self._is_process_running(process.pid):
                    console.print(f"[red]✗ Node {node_name} crashed immediately![/red]")
                    console.print(f"[yellow]Check logs: {log_file}[/yellow]")
                    return False

                # Quick bind check for admin port
                try:
                    with socket.create_connection(
                        ("127.0.0.1", int(rpc_port)), timeout=1.5
                    ):
                        console.print(
                            f"[green]✓ Admin server reachable at http://localhost:{rpc_port}/admin-dashboard[/green]"
                        )
                except Exception:
                    console.print(
                        f"[yellow]⚠ Admin server not reachable yet on http://localhost:{rpc_port}. It may take a few seconds. Check logs if it persists.[/yellow]"
                    )

                return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start node {node_name}: {str(e)}[/red]")
            return False

    def stop_node(self, node_name: str) -> bool:
        """Stop a running node."""
        try:
            # Check if we have the process object
            if node_name in self.processes:
                process = self.processes[node_name]
                try:
                    process.terminate()
                    process.wait(timeout=5)
                    console.print(f"[green]✓ Stopped node {node_name}[/green]")
                except subprocess.TimeoutExpired:
                    console.print(f"[yellow]Force killing node {node_name}...[/yellow]")
                    process.kill()
                    process.wait()
                del self.processes[node_name]
                self._remove_pid_file(node_name)
                self.node_rpc_ports.pop(node_name, None)
                return True

            # Try loading PID from file
            pid = self._load_pid(node_name)
            if pid and self._is_process_running(pid):
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)

                # Check if still running
                if self._is_process_running(pid):
                    console.print(f"[yellow]Force killing node {node_name}...[/yellow]")
                    os.kill(pid, signal.SIGKILL)

                self._remove_pid_file(node_name)
                self.node_rpc_ports.pop(node_name, None)
                console.print(f"[green]✓ Stopped node {node_name}[/green]")
                return True
            else:
                console.print(f"[yellow]Node {node_name} is not running[/yellow]")
                self._remove_pid_file(node_name)
                return False

        except Exception as e:
            console.print(f"[red]✗ Failed to stop node {node_name}: {str(e)}[/red]")
            return False

    def stop_all_nodes(self) -> bool:
        """Stop all running nodes. Returns True on success, False on failure."""
        stopped = 0
        failed_nodes = []

        # Collect all running nodes (from tracked processes and PID files)
        running_nodes = []

        # Check tracked processes
        for node_name in list(self.processes.keys()):
            running_nodes.append(node_name)

        # Check PID files for nodes not already in tracked processes
        for pid_file in self.pid_file_dir.glob("*.pid"):
            node_name = pid_file.stem
            if node_name not in self.processes:
                pid = self._load_pid(node_name)
                if pid and self._is_process_running(pid):
                    running_nodes.append(node_name)
                else:
                    # Clean up stale PID file silently (with exception handling)
                    try:
                        self._remove_pid_file(node_name)
                    except Exception:
                        # Silently ignore cleanup failures (permissions, locked files, etc.)
                        pass

        # If no running nodes found
        if not running_nodes:
            console.print("[yellow]No Calimero nodes are currently running[/yellow]")
            return True

        console.print(f"[bold]Stopping {len(running_nodes)} Calimero nodes...[/bold]")

        # Stop each running node
        for node_name in running_nodes:
            try:
                # Try tracked process first
                if node_name in self.processes:
                    process = self.processes[node_name]
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                        console.print(f"[green]✓ Stopped node {node_name}[/green]")
                    except subprocess.TimeoutExpired:
                        console.print(
                            f"[yellow]Force killing node {node_name}...[/yellow]"
                        )
                        process.kill()
                        process.wait()
                        console.print(f"[green]✓ Stopped node {node_name}[/green]")
                    del self.processes[node_name]
                    self._remove_pid_file(node_name)
                    self.node_rpc_ports.pop(node_name, None)
                    stopped += 1
                else:
                    # Stop by PID file
                    pid = self._load_pid(node_name)
                    if pid and self._is_process_running(pid):
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(2)

                        # Check if still running
                        if self._is_process_running(pid):
                            console.print(
                                f"[yellow]Force killing node {node_name}...[/yellow]"
                            )
                            os.kill(pid, signal.SIGKILL)

                        console.print(f"[green]✓ Stopped node {node_name}[/green]")
                    else:
                        # Process stopped between check and stop attempt (race condition)
                        # Still need to clean up resources
                        console.print(
                            f"[cyan]Node {node_name} already stopped, cleaning up...[/cyan]"
                        )

                    # Always clean up PID file and RPC port for PID-tracked nodes
                    self._remove_pid_file(node_name)
                    self.node_rpc_ports.pop(node_name, None)

                    # Increment counter only after successful cleanup
                    stopped += 1
            except Exception as e:
                console.print(f"[red]✗ Failed to stop {node_name}: {str(e)}[/red]")
                failed_nodes.append(node_name)

        console.print(
            f"\n[bold]Stop Summary: {stopped}/{len(running_nodes)} nodes stopped successfully[/bold]"
        )

        # Return False only if there were actual failures
        if failed_nodes:
            console.print(f"[red]Failed to stop: {', '.join(failed_nodes)}[/red]")
            return False

        return True

    def list_nodes(self) -> list:
        """List all running nodes."""
        nodes = []

        # Check PID files
        for pid_file in self.pid_file_dir.glob("*.pid"):
            node_name = pid_file.stem
            pid = self._load_pid(node_name)
            if pid and self._is_process_running(pid):
                rpc_port = self._read_rpc_port(node_name) or "unknown"
                nodes.append(
                    {
                        "name": node_name,
                        "pid": pid,
                        "status": "running",
                        "mode": "binary",
                        "rpc_port": rpc_port,
                        "admin_url": (
                            f"http://localhost:{rpc_port}/admin-dashboard"
                            if isinstance(rpc_port, int)
                            or (isinstance(rpc_port, str) and rpc_port.isdigit())
                            else ""
                        ),
                    }
                )

        return nodes

    def _read_rpc_port(self, node_name: str) -> Optional[int]:
        """Best-effort read RPC port from config.toml under the node data dir."""
        try:
            node_dir = Path(f"./data/{node_name}") / node_name
            config_path = node_dir / "config.toml"
            if not config_path.exists():
                return None

            with open(config_path) as f:
                content = f.read()
            # Try a few common patterns
            patterns = [
                r"server_port\s*=\s*(\d+)",
                r"server-port\s*=\s*(\d+)",
                r"server\.port\s*=\s*(\d+)",
                r"admin_port\s*=\s*(\d+)",
                r"rpc_port\s*=\s*(\d+)",
            ]
            for pat in patterns:
                m = re.search(pat, content)
                if m:
                    try:
                        return int(m.group(1))
                    except ValueError:
                        pass
            return None
        except Exception:
            return None

    def is_node_running(self, node_name: str) -> bool:
        """Check if a node is running."""
        pid = self._load_pid(node_name)
        return pid is not None and self._is_process_running(pid)

    def get_node_rpc_port(self, node_name: str) -> Optional[int]:
        """Return the RPC port for a node if known."""
        if node_name in self.node_rpc_ports:
            return self.node_rpc_ports[node_name]

        port = self._read_rpc_port(node_name)
        if port is not None:
            self.node_rpc_ports[node_name] = port
        return port

    def get_node_logs(self, node_name: str, lines: int = 50) -> Optional[str]:
        """Get the last N lines of node logs."""
        data_dir = Path(f"./data/{node_name}")
        log_file = data_dir / "logs" / f"{node_name}.log"

        if not log_file.exists():
            return None

        try:
            # Read last N lines
            with open(log_file) as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
        except Exception as e:
            console.print(f"[red]Error reading logs: {e}[/red]")
            return None

    def follow_node_logs(self, node_name: str, tail: int = 100) -> bool:
        """Stream logs for a node in real time (tail -f behavior)."""
        from rich.console import Console

        data_dir = Path(f"./data/{node_name}")
        log_file = data_dir / "logs" / f"{node_name}.log"

        console = Console()

        try:
            # Wait briefly if log file doesn't exist yet
            timeout_seconds = 10
            start_time = time.time()
            while (
                not log_file.exists() and (time.time() - start_time) < timeout_seconds
            ):
                time.sleep(0.25)

            if not log_file.exists():
                console.print(
                    f"[yellow]No logs found for {node_name}. Ensure the node is running and check {log_file}[/yellow]"
                )
                return False

            with open(log_file) as f:
                # Seek to show last `tail` lines first
                if tail is not None and tail > 0:
                    try:
                        # Read last N lines efficiently
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        block_size = 1024
                        data = ""
                        bytes_to_read = min(file_size, block_size)
                        while bytes_to_read > 0 and data.count("\n") <= tail:
                            f.seek(f.tell() - bytes_to_read)
                            data = f.read(bytes_to_read) + data
                            f.seek(f.tell() - bytes_to_read)
                            if f.tell() == 0:
                                break
                            bytes_to_read = min(f.tell(), block_size)
                        lines_buf = data.splitlines()[-tail:]
                        for line in lines_buf:
                            console.print(line)
                    except Exception:
                        # Fallback: read all and slice
                        f.seek(0)
                        lines_buf = f.readlines()[-tail:]
                        for line in lines_buf:
                            console.print(line.rstrip("\n"))

                # Now follow appended content
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.25)
                        continue
                    console.print(line.rstrip("\n"))

        except KeyboardInterrupt:
            return True
        except Exception as e:
            console.print(f"[red]Error streaming logs: {e}[/red]")
            return False

    def run_multiple_nodes(
        self,
        count: int,
        base_port: int = 2428,
        base_rpc_port: int = 2528,
        chain_id: str = "testnet-1",
        prefix: str = "calimero-node",
        image: Optional[str] = None,  # Ignored in binary mode
        auth_service: bool = False,  # Not supported in binary mode
        auth_image: Optional[str] = None,  # Ignored
        auth_use_cached: bool = False,  # Ignored
        webui_use_cached: bool = False,  # Ignored
        log_level: str = "debug",
        rust_backtrace: str = "0",
        mock_relayer: bool = False,  # Ignored
        workflow_id: Optional[str] = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        near_devnet_config: dict = None,  # Enable NEAR Devnet
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
    ) -> bool:
        """
        Start multiple nodes with sequential naming.

        Args:
            count: Number of nodes to start
            base_port: Base P2P port (each node gets base_port + index)
            base_rpc_port: Base RPC port (each node gets base_rpc_port + index)
            chain_id: Blockchain chain ID
            prefix: Node name prefix
            image: Ignored (binary mode doesn't use Docker images)
            auth_service: Not supported in binary mode
            auth_use_cached: Ignored
            webui_use_cached: Ignored
            log_level: RUST_LOG level
            rust_backtrace: RUST_BACKTRACE level

        Returns:
            True if all nodes started successfully
        """
        if auth_service:
            console.print(
                "[yellow]⚠ Auth service is not supported in binary mode (--no-docker)[/yellow]"
            )

        console.print(f"[cyan]Starting {count} nodes with prefix '{prefix}'...[/cyan]")

        # Generate a single shared workflow_id for all nodes if none provided
        if workflow_id is None:
            workflow_id = str(uuid.uuid4())[:8]
            console.print(f"[cyan]Generated shared workflow_id: {workflow_id}[/cyan]")

        success_count = 0

        # Use dynamic port allocation for e2e mode to avoid conflicts
        if e2e_mode:
            # Find available ports dynamically
            allocated_ports = self._find_available_ports(
                count * 2
            )  # Need P2P + RPC for each node
            console.print(f"[cyan]Allocated dynamic ports: {allocated_ports}[/cyan]")
        else:
            # Default base ports if None provided (legacy behavior)
            if base_port is None:
                base_port = 2428
            if base_rpc_port is None:
                base_rpc_port = 2528
            allocated_ports = []

        for i in range(count):
            node_name = f"{prefix}-{i+1}"
            if e2e_mode:
                # Use dynamically allocated ports
                port = allocated_ports[i * 2]  # P2P port
                rpc_port = allocated_ports[i * 2 + 1]  # RPC port
            else:
                # Use fixed port ranges (legacy behavior)
                port = base_port + i
                rpc_port = base_rpc_port + i

            # Resolve specific config for this node if a map is provided
            node_specific_near_config = None
            if near_devnet_config:
                if node_name in near_devnet_config:
                    node_specific_near_config = near_devnet_config[node_name]

            if self.run_node(
                node_name=node_name,
                port=port,
                rpc_port=rpc_port,
                chain_id=chain_id,
                log_level=log_level,
                rust_backtrace=rust_backtrace,
                mock_relayer=mock_relayer,
                workflow_id=workflow_id,
                e2e_mode=e2e_mode,
                near_devnet_config=node_specific_near_config,
                bootstrap_nodes=bootstrap_nodes,
            ):
                success_count += 1
            else:
                console.print(f"[red]✗ Failed to start node {node_name}[/red]")
                return False

        console.print(
            f"\n[bold green]✓ Successfully started all {success_count} node(s)[/bold green]"
        )
        return True

    def force_pull_image(self, image: str) -> bool:
        """
        No-op for binary mode (no Docker images to pull).

        Args:
            image: Ignored

        Returns:
            True (always succeeds as it's a no-op)
        """
        # Binary mode doesn't use Docker images
        return True

    def verify_admin_binding(self, node_name: str) -> bool:
        """
        Verify admin API binding for a node.

        Args:
            node_name: Name of the node to verify

        Returns:
            True if node is running (admin API verification not implemented for binary mode)
        """
        # For binary mode, just check if the process is running
        return self.is_node_running(node_name)

    def _apply_bootstrap_nodes(
        self,
        config_file: Path,
        node_name: str,
        bootstrap_nodes: list[str],
    ):
        """Apply bootstrap nodes configuration."""
        try:
            import toml

            if not config_file.exists():
                console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
                return

            with open(config_file) as f:
                config = toml.load(f)

            self._set_nested_config(config, "bootstrap.nodes", bootstrap_nodes)

            import stat

            if config_file.exists():
                config_file.chmod(config_file.stat().st_mode | stat.S_IWUSR)

            with open(config_file, "w") as f:
                toml.dump(config, f)

            console.print(
                f"[green]✓ Applied bootstrap nodes to {node_name} ({len(bootstrap_nodes)} nodes)[/green]"
            )

        except ImportError:
            console.print(
                "[red]✗ toml package not found. Install with: pip install toml[/red]"
            )
        except Exception as e:
            console.print(
                f"[red]✗ Failed to apply bootstrap nodes to {node_name}: {e}[/red]"
            )

    def _apply_e2e_defaults(
        self,
        config_file: Path,
        node_name: str,
        workflow_id: Optional[str],
    ):
        """Apply e2e-style defaults for reliable testing."""
        try:
            # Generate unique workflow ID if not provided
            if not workflow_id:
                workflow_id = str(uuid.uuid4())[:8]

            # Check if config file exists
            if not config_file.exists():
                console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
                return

            # Load existing config
            with open(config_file) as f:
                config = toml.load(f)

            # Apply e2e-style defaults for reliable testing
            e2e_config = {
                # Disable bootstrap nodes for test isolation
                "bootstrap.nodes": [],
                # Use unique rendezvous namespace per workflow (like e2e tests)
                "discovery.rendezvous.namespace": f"calimero/merobox-tests/{workflow_id}",
                # Keep mDNS as backup (like e2e tests)
                "discovery.mdns": True,
                # Aggressive sync settings from e2e tests for reliable testing
                "sync.timeout_ms": 30000,  # 30s timeout (matches production)
                # 500ms between syncs (very aggressive for tests)
                "sync.interval_ms": 500,
                # 1s periodic checks (ensures rapid sync in tests)
                "sync.frequency_ms": 1000,
                # Ethereum local devnet configuration (same as e2e tests)
                "context.config.ethereum.network": "sepolia",
                "context.config.ethereum.contract_id": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
                "context.config.ethereum.signer": "self",
                "context.config.signer.self.ethereum.sepolia.rpc_url": "http://127.0.0.1:8545",
                "context.config.signer.self.ethereum.sepolia.account_id": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                "context.config.signer.self.ethereum.sepolia.secret_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            }

            # Apply each configuration
            for key, value in e2e_config.items():
                self._set_nested_config(config, key, value)

            # Write back to file (ensure it's writable first)
            if config_file.exists():
                config_file.chmod(config_file.stat().st_mode | stat.S_IWUSR)

            with open(config_file, "w") as f:
                toml.dump(config, f)

            console.print(
                f"[green]✓ Applied e2e-style defaults to {node_name} (workflow: {workflow_id})[/green]"
            )

        except ImportError:
            console.print(
                "[red]✗ toml package not found. Install with: pip install toml[/red]"
            )
        except Exception as e:
            console.print(
                f"[red]✗ Failed to apply e2e defaults to {node_name}: {e}[/red]"
            )

    def _find_available_ports(self, count: int) -> list[int]:
        """Find available ports for dynamic allocation."""
        ports = []
        start_port = 3000  # Start from a higher range to avoid common conflicts

        for port in range(
            start_port, start_port + 10000
        ):  # Search in a reasonable range
            if len(ports) >= count:
                break

            # Check if port is available
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    ports.append(port)
                except OSError:
                    # Port is in use, try next one
                    continue

        if len(ports) < count:
            raise RuntimeError(f"Could not find {count} available ports")

        return ports

    def _set_nested_config(self, config: dict, key: str, value):
        """Set nested configuration value using dot notation."""
        keys = key.split(".")
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value
        console.print(f"[cyan]  {key} = {value}[/cyan]")

    def _apply_near_devnet_config(
        self,
        config_file: Path,
        node_name: str,
        rpc_url: str,
        contract_id: str,
        account_id: str,
        pub_key: str,
        secret_key: str,
    ):
        """Wrapper for shared config utility."""
        return apply_near_devnet_config_to_file(
            config_file,
            node_name,
            rpc_url,
            contract_id,
            account_id,
            pub_key,
            secret_key,
        )
