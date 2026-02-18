"""
Binary Manager - Manages Calimero nodes as native processes (no Docker).
"""

import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from rich.console import Console

from merobox.commands.config_utils import (
    apply_bootstrap_nodes,
    apply_e2e_defaults,
    apply_near_devnet_config_to_file,
)
from merobox.commands.constants import (
    DEFAULT_P2P_PORT,
    DEFAULT_RPC_PORT,
    PROCESS_WAIT_TIMEOUT,
    SOCKET_CONNECTION_TIMEOUT,
)

console = Console()


class BinaryManager:
    """Manages Calimero nodes as native binary processes."""

    def __init__(
        self,
        binary_path: Optional[str] = None,
        require_binary: bool = True,
        enable_signal_handlers: bool = True,
    ):
        """
        Initialize the BinaryManager.

        Args:
            binary_path: Path to the merod binary. If None, searches PATH.
            require_binary: If True, exit if binary not found. If False, set to None gracefully.
            enable_signal_handlers: If True, register signal handlers for graceful
                shutdown on SIGINT/SIGTERM. Set to False in tests or when managing
                signals externally.
        """
        if (
            binary_path
            and os.path.isfile(binary_path)
            and os.access(binary_path, os.X_OK)
        ):
            self.binary_path = binary_path
        else:
            if binary_path:
                console.print(
                    f"[yellow]Warning: merod binary not found at {binary_path!r}, searching PATH[/yellow]"
                )
            self.binary_path = self._find_binary(require=require_binary)

        self.processes = {}  # node_name -> subprocess.Popen
        self.node_rpc_ports: dict[str, int] = {}
        self.pid_file_dir = Path("./data/.pids")
        self.pid_file_dir.mkdir(parents=True, exist_ok=True)
        self._shutting_down = False
        self._original_sigint_handler = None
        self._original_sigterm_handler = None

        if enable_signal_handlers:
            self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown."""
        # Store original handlers so we can restore them if needed
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._signal_handler
        )
        self._original_sigterm_handler = signal.signal(
            signal.SIGTERM, self._signal_handler
        )
        # Also register atexit handler for cleanup on normal exit
        atexit.register(self._cleanup_on_exit)

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM signals for graceful shutdown."""
        if self._shutting_down:
            # Already shutting down, force exit on second signal
            console.print("\n[red]Forced exit requested, terminating...[/red]")
            sys.exit(1)

        self._shutting_down = True
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        console.print(
            f"\n[yellow]Received {sig_name}, initiating graceful shutdown...[/yellow]"
        )

        self._cleanup_resources()

        # Exit cleanly
        sys.exit(0)

    def _cleanup_on_exit(self):
        """Cleanup handler for atexit - only runs if not already cleaned up."""
        if not self._shutting_down:
            self._cleanup_resources()

    def _cleanup_resources(self):
        """Stop all managed processes."""
        if self.processes:
            console.print("[cyan]Stopping managed processes...[/cyan]")
            for node_name in list(self.processes.keys()):
                try:
                    process = self.processes[node_name]
                    process.terminate()
                    try:
                        process.wait(timeout=PROCESS_WAIT_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    console.print(f"[green]✓ Stopped process {node_name}[/green]")
                    self._remove_pid_file(node_name)
                except Exception as e:
                    console.print(
                        f"[yellow]⚠️  Could not stop process {node_name}: {e}[/yellow]"
                    )
            self.processes.clear()
            self.node_rpc_ports.clear()

    def remove_signal_handlers(self):
        """Remove signal handlers and restore original handlers."""
        if self._original_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None
        if self._original_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm_handler)
            self._original_sigterm_handler = None

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
        port: int = DEFAULT_P2P_PORT,
        rpc_port: int = DEFAULT_RPC_PORT,
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
        workflow_id: Optional[str] = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        config_path: Optional[str] = None,  # custom config.toml path
        near_devnet_config: dict = None,  # Enable NEAR Devnet
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        auth_mode: Optional[str] = None,  # Authentication mode (embedded, proxy)
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
            auth_mode: Authentication mode ('embedded' or 'proxy'). When 'embedded',
                enables built-in auth with JWT protection on all endpoints.

        Returns:
            True if successful, False otherwise
        """
        try:
            # Default ports if None provided
            if port is None:
                port = DEFAULT_P2P_PORT
            if rpc_port is None:
                rpc_port = DEFAULT_RPC_PORT

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
            if auth_mode == "embedded":
                console.print(
                    f"[cyan]  Auth mode: {auth_mode} (JWT protection enabled on all endpoints)[/cyan]"
                )
                console.print(
                    "[yellow]  Note: Embedded auth uses username/password provider by default. "
                    "Auth data stored at <node_home>/auth/[/yellow]"
                )

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
                        "--node",
                        node_name,
                        "init",
                        "--server-port",
                        str(rpc_port),
                        "--swarm-port",
                        str(port),
                    ]
                    # Add auth mode to init command if specified
                    if auth_mode:
                        init_cmd.extend(["--auth-mode", auth_mode])
                    with open(log_file, "a", encoding="utf-8") as log_f:
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
                apply_e2e_defaults(actual_config_file, node_name, workflow_id)

            # Apply bootstrap nodes configuration (works regardless of e2e_mode)
            if bootstrap_nodes:
                apply_bootstrap_nodes(actual_config_file, node_name, bootstrap_nodes)

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
                "--node",
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
                with open(log_file, "a", encoding="utf-8") as log_f:
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
                if auth_mode == "embedded":
                    console.print(
                        f"[cyan]  Auth endpoints: http://localhost:{rpc_port}/auth (register/login)[/cyan]"
                    )
                    console.print(
                        "[yellow]  All API endpoints require a valid JWT token when embedded auth is enabled[/yellow]"
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
                        ("127.0.0.1", int(rpc_port)), timeout=SOCKET_CONNECTION_TIMEOUT
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
                    process.wait(timeout=PROCESS_WAIT_TIMEOUT)
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
                        process.wait(timeout=PROCESS_WAIT_TIMEOUT)
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

            with open(config_path, encoding="utf-8") as f:
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
            with open(log_file, encoding="utf-8") as f:
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

            with open(log_file, encoding="utf-8") as f:
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
        base_port: int = DEFAULT_P2P_PORT,
        base_rpc_port: int = DEFAULT_RPC_PORT,
        chain_id: str = "testnet-1",
        prefix: str = "calimero-node",
        image: Optional[str] = None,  # Ignored in binary mode
        auth_service: bool = False,  # Not supported in binary mode
        auth_image: Optional[str] = None,  # Ignored
        auth_use_cached: bool = False,  # Ignored
        webui_use_cached: bool = False,  # Ignored
        log_level: str = "debug",
        rust_backtrace: str = "0",
        workflow_id: Optional[str] = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        near_devnet_config: dict = None,  # Enable NEAR Devnet
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        auth_mode: Optional[str] = None,  # Authentication mode (embedded, proxy)
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
            auth_mode: Authentication mode ('embedded' or 'proxy')

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
                base_port = DEFAULT_P2P_PORT
            if base_rpc_port is None:
                base_rpc_port = DEFAULT_RPC_PORT
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
                workflow_id=workflow_id,
                e2e_mode=e2e_mode,
                near_devnet_config=node_specific_near_config,
                bootstrap_nodes=bootstrap_nodes,
                auth_mode=auth_mode,
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
