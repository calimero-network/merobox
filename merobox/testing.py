"""
Testing helpers for using Merobox as a lightweight test harness.

Provides:
- cluster: context manager to start/stop a set of Calimero nodes for tests
- workflow: context manager to run a workflow as pretest setup
- nodes: decorator to create pytest fixtures with clean syntax
- run_workflow: decorator to create workflow-based pytest fixtures
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, TypedDict, Union

from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import get_node_rpc_url, console
from merobox.commands.bootstrap.run.executor import WorkflowExecutor


class ClusterEnv(TypedDict):
    nodes: List[str]
    endpoints: Dict[str, str]
    manager: CalimeroManager


class WorkflowEnv(TypedDict):
    nodes: List[str]
    endpoints: Dict[str, str]
    manager: CalimeroManager
    workflow_result: bool


@contextmanager
def cluster(
    count: int = 1,
    *,
    prefix: str = "test-node",
    image: Optional[str] = None,
    chain_id: str = "testnet-1",
    base_port: Optional[int] = None,
    base_rpc_port: Optional[int] = None,
    stop_all: bool = True,
) -> ClusterEnv:
    """Start a temporary Calimero cluster for tests and tear it down automatically.

    Args:
        count: Number of nodes to start.
        prefix: Node name prefix (nodes are named `<prefix>-<i>`).
        image: Docker image to use.
        chain_id: Chain ID to use for nodes.
        base_port: Optional base P2P port to start from (auto-detect if None).
        base_rpc_port: Optional base RPC port to start from (auto-detect if None).
        stop_all: Whether to stop and remove nodes on exit.

    Yields:
        ClusterEnv with node names, endpoints map and the underlying manager.
    """
    manager = CalimeroManager()
    node_names: List[str] = [f"{prefix}-{i+1}" for i in range(count)]

    # Start nodes
    ok = manager.run_multiple_nodes(
        count=count,
        base_port=base_port,
        base_rpc_port=base_rpc_port,
        chain_id=chain_id,
        prefix=prefix,
        image=image,
    )
    if not ok:
        # Best-effort cleanup if any started
        if stop_all:
            for node in node_names:
                try:
                    manager.stop_node(node)
                except Exception:
                    pass
        raise RuntimeError("Failed to start Merobox cluster")

    try:
        endpoints: Dict[str, str] = {
            n: get_node_rpc_url(n, manager) for n in node_names
        }
        yield ClusterEnv(nodes=node_names, endpoints=endpoints, manager=manager)
    finally:
        if stop_all:
            for node in node_names:
                try:
                    manager.stop_node(node)
                except Exception:
                    pass


@contextmanager
def workflow(
    workflow_path: Union[str, Path],
    *,
    prefix: str = "test-node",
    image: Optional[str] = None,
    chain_id: str = "testnet-1",
    base_port: Optional[int] = None,
    base_rpc_port: Optional[int] = None,
    stop_all: bool = True,
    wait_for_ready: bool = True,
) -> WorkflowEnv:
    """Run a Merobox workflow as pretest setup and tear down automatically.

    Args:
        workflow_path: Path to the workflow YAML file.
        prefix: Node name prefix for any nodes created by the workflow.
        image: Docker image to use for nodes.
        chain_id: Blockchain chain ID.
        base_port: Optional base P2P port to start from (auto-detect if None).
        base_rpc_port: Optional base RPC port to start from (auto-detect if None).
        stop_all: Whether to stop and remove nodes on exit.
        wait_for_ready: Whether to wait for nodes to be ready after workflow execution.

    Yields:
        WorkflowEnv with node names, endpoints map, manager, and workflow execution result.
    """
    workflow_path = Path(workflow_path)
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

    manager = CalimeroManager()

    try:
        # Execute the workflow
        console.print(f"[blue]Running workflow: {workflow_path.name}[/blue]")

        # Load workflow configuration
        from merobox.commands.bootstrap.config import load_workflow_config

        config = load_workflow_config(str(workflow_path))

        executor = WorkflowExecutor(config, manager)
        workflow_result = asyncio.run(executor.execute_workflow())

        if not workflow_result:
            console.print(f"[red]Workflow execution failed: {workflow_path.name}[/red]")
            raise RuntimeError(f"Workflow execution failed: {workflow_path.name}")

        console.print(
            f"[green]✓ Workflow executed successfully: {workflow_path.name}[/green]"
        )

        # Get running nodes from the workflow
        running_nodes = manager.get_running_nodes()
        if not running_nodes:
            console.print(
                "[yellow]Warning: No nodes found running after workflow execution[/yellow]"
            )
            running_nodes = []

        # Filter nodes by prefix if specified
        if prefix != "test-node":
            running_nodes = [n for n in running_nodes if n.startswith(prefix)]

        # Wait for nodes to be ready if requested
        if wait_for_ready and running_nodes:
            console.print("[blue]Waiting for nodes to be ready...[/blue]")
            # Simple wait - in practice you might want more sophisticated health checking
            import time

            time.sleep(5)  # Basic wait for services to start

        endpoints: Dict[str, str] = {
            n: get_node_rpc_url(n, manager) for n in running_nodes
        }

        yield WorkflowEnv(
            nodes=running_nodes,
            endpoints=endpoints,
            manager=manager,
            workflow_result=workflow_result,
        )

    finally:
        if stop_all:
            # Stop all nodes that were created
            all_nodes = manager.get_running_nodes()
            for node in all_nodes:
                try:
                    manager.stop_node(node)
                except Exception:
                    pass


def pytest_cluster(
    *,
    count: int = 1,
    prefix: str = "test-node",
    image: Optional[str] = None,
    chain_id: str = "testnet-1",
    base_port: Optional[int] = None,
    base_rpc_port: Optional[int] = None,
    scope: str = "function",
):
    """Factory to create a pytest fixture backed by the cluster context manager.

    Usage in your conftest.py:

        from merobox.testing import pytest_cluster
        merobox_cluster = pytest_cluster(count=2, scope="session")

    And in tests:

        def test_something(merobox_cluster):
            nodes = merobox_cluster["nodes"]
            endpoints = merobox_cluster["endpoints"]
            # ... test logic ...
    """
    import pytest

    @pytest.fixture(scope=scope)
    def _cluster():
        with cluster(
            count=count,
            prefix=prefix,
            image=image,
            chain_id=chain_id,
            base_port=base_port,
            base_rpc_port=base_rpc_port,
        ) as env:
            yield env

    return _cluster


def pytest_workflow(
    *,
    workflow_path: Union[str, Path],
    prefix: str = "test-node",
    image: Optional[str] = None,
    chain_id: str = "testnet-1",
    base_port: Optional[int] = None,
    base_rpc_port: Optional[int] = None,
    scope: str = "function",
    wait_for_ready: bool = True,
):
    """Factory to create a pytest fixture backed by the workflow context manager.

    Usage in your conftest.py:

        from merobox.testing import pytest_workflow
        merobox_workflow = pytest_workflow(
            workflow_path="workflow-examples/workflow-example.yml",
            scope="session"
        )

    And in tests:

        def test_something(merobox_workflow):
            nodes = merobox_workflow["nodes"]
            endpoints = merobox_workflow["endpoints"]
            workflow_result = merobox_workflow["workflow_result"]
            # ... test logic ...
    """
    import pytest

    @pytest.fixture(scope=scope)
    def _workflow():
        with workflow(
            workflow_path=workflow_path,
            prefix=prefix,
            image=image,
            chain_id=chain_id,
            base_port=base_port,
            base_rpc_port=base_rpc_port,
            wait_for_ready=wait_for_ready,
        ) as env:
            yield env

    return _workflow


# ============================================================================
# Cleaner, more Pythonic API
# ============================================================================


def nodes(count: int = 1, *, prefix: str = "test", scope: str = "function", **kwargs):
    """
    Decorator to create a clean pytest fixture for Calimero nodes.

    Usage:
        @nodes(count=2, scope="session")
        def my_cluster():
            '''Two nodes available for the entire test session'''
            pass

        def test_something(my_cluster):
            nodes = my_cluster.nodes
            endpoints = my_cluster.endpoints
    """

    def decorator(func):
        import pytest

        @pytest.fixture(scope=scope)
        def _fixture():
            with cluster(count=count, prefix=prefix, **kwargs) as env:
                # Create a more convenient access object
                class NodeCluster:
                    def __init__(self, env):
                        self.nodes = env["nodes"]
                        self.endpoints = env["endpoints"]
                        self.manager = env["manager"]

                    def __getitem__(self, key):
                        # Backward compatibility
                        return {
                            "nodes": self.nodes,
                            "endpoints": self.endpoints,
                            "manager": self.manager,
                        }[key]

                    def node(self, index_or_name):
                        """Get a specific node by index or name"""
                        if isinstance(index_or_name, int):
                            return self.nodes[index_or_name]
                        return index_or_name

                    def endpoint(self, index_or_name):
                        """Get endpoint for a specific node"""
                        node_name = self.node(index_or_name)
                        return self.endpoints[node_name]

                yield NodeCluster(env)

        # Copy function metadata
        _fixture.__name__ = func.__name__
        _fixture.__doc__ = func.__doc__ or f"Merobox cluster with {count} nodes"

        return _fixture

    return decorator


def run_workflow(workflow_path: Union[str, Path], *, scope: str = "function", **kwargs):
    """
    Decorator to create a clean pytest fixture that runs a workflow.

    Usage:
        @run_workflow("my-workflow.yml", scope="session")
        def my_setup():
            '''Workflow setup for testing'''
            pass

        def test_something(my_setup):
            assert my_setup.success
            nodes = my_setup.nodes
    """

    def decorator(func):
        import pytest

        @pytest.fixture(scope=scope)
        def _fixture():
            with workflow(workflow_path, **kwargs) as env:
                # Create a more convenient access object
                class WorkflowEnvironment:
                    def __init__(self, env):
                        self.nodes = env["nodes"]
                        self.endpoints = env["endpoints"]
                        self.manager = env["manager"]
                        self.success = env["workflow_result"]

                    def __getitem__(self, key):
                        # Backward compatibility
                        return {
                            "nodes": self.nodes,
                            "endpoints": self.endpoints,
                            "manager": self.manager,
                            "workflow_result": self.success,
                        }[key]

                    def node(self, index_or_name):
                        """Get a specific node by index or name"""
                        if isinstance(index_or_name, int):
                            return self.nodes[index_or_name]
                        return index_or_name

                    def endpoint(self, index_or_name):
                        """Get endpoint for a specific node"""
                        node_name = self.node(index_or_name)
                        return self.endpoints[node_name]

                yield WorkflowEnvironment(env)

        # Copy function metadata
        _fixture.__name__ = func.__name__
        _fixture.__doc__ = func.__doc__ or f"Workflow environment from {workflow_path}"

        return _fixture

    return decorator


def using(*fixtures):
    """
    Helper to combine multiple test fixtures cleanly.

    Usage:
        @nodes(count=2)
        def cluster():
            pass

        @run_workflow("setup.yml")
        def workflow_env():
            pass

        def test_combined(using(cluster, workflow_env)):
            # Access both fixtures
            pass
    """
    def wrapper(test_func):
        # This is a placeholder - in practice, you'd use pytest.mark.parametrize 
        # or similar mechanisms to combine fixtures
        return test_func
    return wrapper
