import json
import platform
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import requests
from rich.console import Console
from tqdm import tqdm

from .client import NearDevnetClient

console = Console()

NEAR_SANDBOX_AWS_BASE_URL = (
    "https://s3-us-west-1.amazonaws.com/build.nearprotocol.com/nearcore"
)
NEAR_SANDBOX_VERSION = "2.9.0"


class SandboxManager:
    def __init__(self, home_dir=None):
        self.home_dir = (
            Path(home_dir) if home_dir else Path.home() / ".merobox" / "sandbox"
        )
        self.rpc_port = 3030
        self.process = None
        self.root_client = None

        platform_full_name = self._get_platform_full_name()

        self.binary_path = self.home_dir / platform_full_name / "near-sandbox"

    def _get_platform_url(self):
        platform_full_name = self._get_platform_full_name()

        return f"{NEAR_SANDBOX_AWS_BASE_URL}/{platform_full_name}/{NEAR_SANDBOX_VERSION}/near-sandbox.tar.gz"

    def _get_platform_full_name(self):
        system = platform.system().lower()
        machine = platform.machine().lower()

        # macOS
        # Darwin-x86_64 is not supported by NEAR Sandbox right now.
        # ref: https://github.com/near/near-sandbox-rs/blob/93218d264c2c6ac7be04733b7dbfa4c6047775f7/src/runner/mod.rs#L41
        if system == "darwin" and machine in ["arm64", "aarch64"]:
            return "Darwin-arm64"
        # Linux
        elif system == "linux":
            if machine == "x86_64":
                return "Linux-x86_64"
            elif machine in ["aarch64", "arm64"]:
                return "Linux-aarch64"

        raise Exception(f"Unsupported platform: {system} {machine}")

    def ensure_binary(self):
        if self.binary_path.exists():
            return

        self.home_dir.mkdir(parents=True, exist_ok=True)
        url = self._get_platform_url()
        console.print(f"[yellow]Downloading near-sandbox from {url}...[/yellow]")

        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            tar_path = self.home_dir / "sandbox.tar.gz"
            with (
                open(tar_path, "wb") as f,
                tqdm(
                    desc="Downloading",
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar,
            ):
                for data in response.iter_content(chunk_size=1024):
                    size = f.write(data)
                    bar.update(size)

            console.print("[yellow]Extracting...[/yellow]")
            with tarfile.open(tar_path) as tar:
                tar.extractall(path=self.home_dir)

            # Clean up tar and ensure binary is executable
            tar_path.unlink()
            self.binary_path.chmod(0o755)
            console.print("[green]✓ near-sandbox installed[/green]")
        except Exception as e:
            console.print(f"[red]Failed to download sandbox: {e}[/red]")
            # Cleanup partial download
            if "tar_path" in locals() and tar_path.exists():
                tar_path.unlink()
            raise

    def start(self):
        self.ensure_binary()

        # Ensure no stale processes are holding the port/state
        self._cleanup_stale_sandbox()

        # Reset data dir for clean state
        data_dir = self.home_dir / "data"
        if data_dir.exists():
            shutil.rmtree(data_dir)

        # Init sandbox
        init_cmd = [str(self.binary_path), "--home", str(data_dir), "init"]
        subprocess.run(
            init_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        console.print(
            f"[yellow]Starting near-sandbox on port {self.rpc_port}...[/yellow]"
        )
        run_cmd = [
            str(self.binary_path),
            "--home",
            str(data_dir),
            "run",
            "--rpc-addr",
            f"0.0.0.0:{self.rpc_port}",
        ]

        self.process = subprocess.Popen(
            run_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        if not self._wait_for_rpc():
            self.stop_process()
            raise Exception("Sandbox failed to start")

        # Initialize Root Client (test.near) using generated key
        key_path = data_dir / "validator_key.json"
        with open(key_path) as f:
            kd = json.load(f)

        self.root_client = NearDevnetClient(
            self.get_rpc_url(), kd["account_id"], kd["secret_key"]
        )

        console.print(
            f"[green]✓ NEAR Sandbox running. Root: {self.root_client.account.account_id}[/green]"
        )

    def _cleanup_stale_sandbox(self):
        """Kill any existing near-sandbox processes to prevent state conflicts."""
        try:
            # Force kill existing sandbox processes
            # This ensures we don't connect to a stale process with old state
            subprocess.run(
                ["pkill", "-9", "near-sandbox"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for OS to release the port
            time.sleep(0.5)
        except Exception:
            # pkill might fail or not be present, which is fine if no process exists
            pass

    async def stop(self):
        """Async stop that closes clients and process."""
        try:
            if self.root_client:
                await self.root_client.close()
        finally:
            # Ensure process is stopped even if client close fails
            self.stop_process()

    def stop_process(self):
        """Synchronously stop the sandbox process."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            console.print("[yellow]NEAR Sandbox stopped[/yellow]")
            self.process = None

    def _wait_for_rpc(self, timeout=10):
        # Sleep a little bit to wait for a RPC spin up.
        time.sleep(1.0)
        start = time.time()

        while time.time() - start < timeout:
            try:
                requests.get(f"http://localhost:{self.rpc_port}/status")
                return True
            except Exception as e:
                console.print(
                    f"[red]Failed to get status from sandbox via RPC: {e}[/red]"
                )
                time.sleep(0.1)
        return False

    def get_rpc_url(self, for_docker=False):
        """
        Returns RPC URL.
        If for_docker is True, returns URL accessible from inside a Docker container
        running on the same host.
        """
        if for_docker:
            if platform.system().lower() == "linux":
                # Linux containers can't use host.docker.internal by default without extra flags
                return f"http://host.docker.internal:{self.rpc_port}"
            # Mac Docker Desktop
            return f"http://host.docker.internal:{self.rpc_port}"
        return f"http://localhost:{self.rpc_port}"

    async def setup_calimero(self, ctx_wasm, proxy_wasm):
        """
        Deploys Context Registry and Proxy Lib contracts.
        Returns the contract ID.
        """
        # Create calimero.test.near
        acc_id = "calimero.test.near"
        console.print(f"[cyan]Creating account {acc_id}...[/cyan]")

        creds = await self.root_client.create_account(acc_id, initial_balance=100)

        # Deploy Context Registry Contract
        console.print("[cyan]Deploying Context Registry...[/cyan]")
        calimero_client = NearDevnetClient(
            self.get_rpc_url(), creds["account_id"], creds["secret_key"]
        )

        try:
            await calimero_client.deploy_contract(ctx_wasm)

            # Set Proxy Code
            # The contract expects raw bytes for `set_proxy_code()`
            console.print("[cyan]Setting Proxy Code...[/cyan]")
            with open(proxy_wasm, "rb") as f:
                proxy_code = f.read()

            # The proxy code is passed as raw bytes argument to the method
            await calimero_client.call(acc_id, "set_proxy_code", proxy_code)

            console.print(f"[green]✓ Contracts deployed to {acc_id}[/green]")
            return acc_id
        finally:
            await calimero_client.close()

    async def create_node_account(self, node_name):
        """Creates nodeX.test.near funded account."""
        acc_id = f"{node_name}.test.near"
        return await self.root_client.create_account(acc_id, initial_balance=50)
