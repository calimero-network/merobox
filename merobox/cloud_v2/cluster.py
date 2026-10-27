"""A release-v2 cluster with one `merod` process per node."""

from __future__ import annotations

import gzip
import io
import json
import os
import secrets
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: The origin dev passkeys claim; the cluster allows it.
DEV_ORIGIN = "http://localhost:3000"


def find_binary(binary: str | None = None) -> str:
    """`binary`, else MEROD_BINARY (or the older MEROD_V2_BINARY), else
    `merod` on PATH."""
    candidate = (
        binary
        or os.environ.get("MEROD_BINARY")
        or os.environ.get("MEROD_V2_BINARY")
        or "merod"
    )
    found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
    if not found:
        raise FileNotFoundError(
            f"merod not found ({candidate}); build it from core's release-v2 branch "
            "(cargo build -p merod) or set MEROD_BINARY"
        )
    return found


_RUN: dict[str, bool] = {}


def run_command(binary: str) -> list[str]:
    """How to start a node with flags: `merod run` (v1's command line), or
    the bare binary of an older `merod-v2` build."""
    if binary not in _RUN:
        try:
            out = subprocess.run(
                [binary, "--help"], capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _RUN[binary] = any(
            line.split()[:1] == ["run"]
            for line in out.splitlines()
            if line.startswith("  ")
        )
    return [binary, "run"] if _RUN[binary] else [binary]


def mpk_coords(mpk: bytes) -> tuple[str, str]:
    """`(package, appVersion)` from a `.mpk`'s top-level `manifest.json`."""
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(mpk))) as tar:
        for member in tar.getmembers():
            if member.name.lstrip("./") == "manifest.json":
                f = tar.extractfile(member)
                manifest = json.load(f) if f else {}
                return str(manifest["package"]), str(manifest["appVersion"])
    raise ValueError("not a .mpk: no manifest.json")


class PackageRegistry:
    """A package registry on 127.0.0.1, laid out as the real one is:
    `artifacts/{package}/{version}/{package}-{version}.mpk`. The cluster's
    nodes install from it, and every node that runs a release fetches it here."""

    def __init__(self, root: Path):
        self.root = root
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        handler = partial(_QuietHandler, directory=str(root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, mpk: bytes) -> tuple[str, str]:
        """Put a `.mpk` where the nodes fetch it; answers its (package, version)."""
        package, version = mpk_coords(mpk)
        d = self.root / "artifacts" / package / version
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{package}-{version}.mpk").write_bytes(mpk)
        return package, version

    def hits(self) -> list[str]:
        return list(_QuietHandler.paths)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class _QuietHandler(SimpleHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        _QuietHandler.paths.append(self.path)
        super().do_GET()

    def log_message(self, *args) -> None:
        pass


def supports(binary: str, flag: str) -> bool:
    """Whether the node's start command lists `flag` (older builds lack some)."""
    try:
        out = subprocess.run(
            [*run_command(binary), "--help"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return flag in out


class ProcessCluster:
    """N `merod run --dev` nodes on 127.0.0.1:base_port+1..N, each its own process.

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
        #: The registry the nodes install signed packages from, once started
        #: (a build without `--registry-url` gets none).
        self.registry: PackageRegistry | None = None

    @property
    def ids(self) -> list[int]:
        return list(range(1, self.size + 1))

    def url(self, node: int) -> str:
        return f"http://127.0.0.1:{self.base_port + node}/"

    def _args(self, node: int) -> list[str]:
        peers = ",".join(f"{i}={self.url(i)}" for i in self.ids)
        args = [
            *run_command(self.binaries[node]),
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
        if self.registry and supports(self.binaries[node], "--registry-url"):
            args += ["--registry-url", self.registry.url]
        if node == 1:
            args.append("--bootstrap")
        return args

    # ------------------------------------------------------------- lifecycle

    def start(self, timeout: float = 60) -> None:
        if self.registry is None and supports(self.binary, "--registry-url"):
            self.registry = PackageRegistry(self.data_dir / "registry")
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
        if self.registry:
            self.registry.stop()
            self.registry = None
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
