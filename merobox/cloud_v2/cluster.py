"""A release-v2 cluster with one `merod-v2` process per node."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

#: The origin dev passkeys claim; the cluster allows it.
DEV_ORIGIN = "http://localhost:3000"


def find_binary(binary: str | None = None) -> str:
    """`binary`, else MEROD_V2_BINARY, else `merod-v2` on PATH."""
    candidate = binary or os.environ.get("MEROD_V2_BINARY") or "merod-v2"
    found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
    if not found:
        raise FileNotFoundError(
            f"merod-v2 not found ({candidate}); build it from core's release-v2 branch "
            "(cargo build -p calimero-cloud-node --bin merod-v2) or set MEROD_V2_BINARY"
        )
    return found


class ProcessCluster:
    """N `merod-v2 --dev` nodes on 127.0.0.1:base_port+1..N, each its own process.

    State lives in `data_dir/node<i>`, so a node can be killed and started again,
    or restarted on another binary (a rolling upgrade), and keep its data.
    """

    def __init__(
        self,
        nodes: int = 3,
        base_port: int = 2700,
        binary: str | None = None,
        data_dir: str | None = None,
        cluster: str = "merobox",
        origins: list | None = None,
        ops_token: str | None = None,
        logs: bool = False,
    ):
        if nodes < 1:
            raise ValueError("a cluster needs at least one node")
        self.size = nodes
        self.base_port = base_port
        self.binary = find_binary(binary)
        self._own_dir = data_dir is None
        self.data_dir = Path(data_dir or tempfile.mkdtemp(prefix="merobox-v2-"))
        self.cluster = cluster
        self.origins = origins or [DEV_ORIGIN]
        self.ops_token = ops_token or secrets.token_hex(16)
        self.logs = logs
        self.procs: dict[int, subprocess.Popen | None] = dict.fromkeys(self.ids)
        self.binaries: dict[int, str] = dict.fromkeys(self.ids, self.binary)

    @property
    def ids(self) -> list[int]:
        return list(range(1, self.size + 1))

    def url(self, node: int) -> str:
        return f"http://127.0.0.1:{self.base_port + node}/"

    def _args(self, node: int) -> list[str]:
        peers = ",".join(f"{i}={self.url(i)}" for i in self.ids)
        args = [
            self.binaries[node],
            "--node-id",
            str(node),
            "--listen",
            f"127.0.0.1:{self.base_port + node}",
            "--public-url",
            self.url(node),
            "--data-dir",
            str(self.data_dir / f"node{node}"),
            "--peers",
            peers,
            "--cluster",
            self.cluster,
            "--dev",
            "--origins",
            ",".join(self.origins),
            "--ops-token",
            self.ops_token,
            "--backup-dir",
            str(self.data_dir / "backups"),
        ]
        if node == 1:
            args.append("--bootstrap")
        return args

    # ------------------------------------------------------------- lifecycle

    def start(self, timeout: float = 60) -> None:
        for i in self.ids:
            self.start_node(i, wait=False)
        self.wait_ready(timeout)

    def start_node(
        self, node: int, binary: str | None = None, wait: bool = True
    ) -> None:
        if self.procs.get(node) is not None:
            raise RuntimeError(f"node {node} is already running")
        if binary:
            self.binaries[node] = find_binary(binary)
        (self.data_dir / f"node{node}").mkdir(parents=True, exist_ok=True)
        out = None if self.logs else subprocess.DEVNULL
        self.procs[node] = subprocess.Popen(self._args(node), stdout=out, stderr=out)
        if wait:
            self.wait_healthy(node)

    def kill_node(self, node: int) -> None:
        """Stop a node abruptly (SIGKILL), as a crash would."""
        proc = self.procs.get(node)
        if proc is None:
            raise RuntimeError(f"node {node} is not running")
        proc.kill()
        proc.wait()
        self.procs[node] = None

    def pause_node(self, node: int) -> None:
        """Freeze a node (SIGSTOP): to its peers it is unreachable, not gone."""
        self._signal(node, signal.SIGSTOP)

    def resume_node(self, node: int) -> None:
        self._signal(node, signal.SIGCONT)

    def restart_node(self, node: int, binary: str | None = None) -> None:
        """Stop a node and start it again on its data, optionally on another binary."""
        if self.procs.get(node) is not None:
            self.kill_node(node)
        self.start_node(node, binary=binary)

    def stop(self) -> None:
        for node, proc in self.procs.items():
            if proc is not None:
                try:
                    proc.send_signal(signal.SIGCONT)
                except ProcessLookupError:
                    pass
                proc.kill()
                proc.wait()
                self.procs[node] = None
        if self._own_dir:
            shutil.rmtree(self.data_dir, ignore_errors=True)

    def running(self) -> list[int]:
        return [i for i, p in self.procs.items() if p is not None and p.poll() is None]

    def _signal(self, node: int, sig: int) -> None:
        proc = self.procs.get(node)
        if proc is None:
            raise RuntimeError(f"node {node} is not running")
        proc.send_signal(sig)

    # ------------------------------------------------------------- readiness

    def health(self, node: int) -> dict | None:
        try:
            with urllib.request.urlopen(f"{self.url(node)}health", timeout=1) as r:
                return json.load(r)
        except OSError:
            return None

    def wait_healthy(self, node: int, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            proc = self.procs.get(node)
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(f"node {node} exited with {proc.returncode}")
            if self.health(node):
                return
            time.sleep(0.2)
        raise TimeoutError(f"node {node} did not answer /health within {timeout}s")

    def wait_ready(self, timeout: float = 60) -> None:
        """Every node up and the system group led, then a moment for the leader
        to register the nodes and mint the first session key."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = [self.health(i) for i in self.ids]
            if all(h) and all(x.get("systemLeader") for x in h):
                time.sleep(1.5)
                return
            for i, proc in self.procs.items():
                if proc is not None and proc.poll() is not None:
                    raise RuntimeError(f"node {i} exited with {proc.returncode}")
            time.sleep(0.2)
        raise TimeoutError(f"the cluster did not form within {timeout}s")

    def __enter__(self) -> ProcessCluster:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
