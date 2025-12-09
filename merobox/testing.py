"""
Merobox testing framework for Python applications.

This module provides utilities for testing applications that interact with
Calimero nodes, including cluster management and workflow execution.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.manager import DockerManager
from merobox.commands.utils import console, get_node_rpc_url

# ============================================================================
# Type definitions
# ============================================================================


class ClusterEnv(TypedDict):
    """Environment for a cluster of Calimero nodes."""

    nodes: list[str]
    endpoints: dict[str, str]
    manager: DockerManager


class WorkflowEnv(TypedDict):
    """Environment for a workflow execution."""

    nodes: list[str]
    endpoints: dict[str, str]
    manager: DockerManager
    workflow_result: bool
    dynamic_values: dict[str, Any] | None


# ============================================================================
# Context managers for testing
# ============================================================================


@contextmanager
def cluster(
    count: int = 1,
    *,
    prefix: str = "test",
    image: str | None = None,
    chain_id: str = "testnet-1",
    base_port: int | None = None,
    base_rpc_port: int | None = None,
    stop_all: bool = True,
    wait_for_ready: bool = True,
    near_devnet: bool = False,
    contracts_dir: str | None = None,
) -> ClusterEnv:
    """Run a cluster of Calimero nodes as pretest setup and tear down automatically.

    Args:
        count: Number of nodes to start.
        prefix: Node name prefix.
        image: Docker image to use for nodes.
        chain_id: Blockchain chain ID.
        base_port: Optional base P2P port to start from (auto-detect if None).
        base_rpc_port: Optional base RPC port to start from (auto-detect if None).
        stop_all: Whether to stop and remove nodes on exit.
        wait_for_ready: Whether to wait for nodes to be ready.

    Yields:
        ClusterEnv with node names, endpoints map, and manager.
    """
    manager = DockerManager()
    sandbox = None
    near_devnet_configs = None

    try:
        if near_devnet:
            sandbox, near_devnet_configs = _setup_near_devnet(
                contracts_dir, count, prefix
            )

        # Use the efficient run_multiple_nodes method instead of manual loop
        success = manager.run_multiple_nodes(
            count=count,
            prefix=prefix,
            image=image,
            chain_id=chain_id,
            base_port=base_port,
            base_rpc_port=base_rpc_port,
            near_devnet_config=near_devnet_configs if near_devnet else None,
        )

        if not success:
            raise RuntimeError(f"Failed to start Merobox cluster with {count} nodes")

        # Get the node names that were actually started
        node_names = [f"{prefix}-{i+1}" for i in range(count)]

        # Wait for nodes to be ready if requested
        if wait_for_ready:
            console.print("[blue]Waiting for nodes to be ready...[/blue]")
            import time

            time.sleep(5)  # Basic wait for services to start

        endpoints: dict[str, Any] = {
            n: get_node_rpc_url(n, manager) for n in node_names
        }

        yield ClusterEnv(nodes=node_names, endpoints=endpoints, manager=manager)

    finally:
        if stop_all:
            # Stop all nodes that were created
            for i in range(count):
                node_name = f"{prefix}-{i+1}"
                try:
                    manager.stop_node(node_name)
                except Exception:
                    pass
        if sandbox:
            # Stop Sandobox
            sandbox.stop_process()


@contextmanager
def workflow(
    workflow_path: str | Path,
    *,
    prefix: str = "test-node",
    image: str | None = None,
    chain_id: str = "testnet-1",
    base_port: int | None = None,
    base_rpc_port: int | None = None,
    stop_all: bool = True,
    wait_for_ready: bool = True,
    near_devnet: bool = False,
    contracts_dir: str | None = None,
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
        WorkflowEnv with node names, endpoints map, manager, workflow execution result, and captured dynamic values.
    """
    workflow_path = Path(workflow_path)
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

    manager = DockerManager()

    # Initialize running_nodes at the beginning to avoid UnboundLocalError
    running_nodes = []

    try:
        # Execute the workflow
        console.print(f"[blue]Running workflow: {workflow_path.name}[/blue]")

        # Load workflow configuration
        from merobox.commands.bootstrap.config import load_workflow_config

        config = load_workflow_config(str(workflow_path))

        executor = WorkflowExecutor(
            config,
            manager,
            near_devnet=near_devnet,
            contracts_dir=contracts_dir,
        )
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

        endpoints: dict[str, Any] = {
            n: get_node_rpc_url(n, manager) for n in running_nodes
        }

        # Create enhanced WorkflowEnv with dynamic values
        workflow_env = WorkflowEnv(
            nodes=running_nodes,
            endpoints=endpoints,
            manager=manager,
            workflow_result=workflow_result,
            dynamic_values=None,
        )

        # Add dynamic values to the environment if available
        if hasattr(executor, "dynamic_values") and executor.dynamic_values:
            workflow_env["dynamic_values"] = executor.dynamic_values
            console.print(
                f"[blue]📋 Captured {len(executor.dynamic_values)} dynamic values from workflow[/blue]"
            )

        yield workflow_env

    finally:
        if stop_all and running_nodes:
            # Stop all nodes that were created
            for node in running_nodes:
                try:
                    manager.stop_node(node)
                except Exception:
                    pass


# ============================================================================
# Pytest fixture decorators
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


def run_workflow(workflow_path: str | Path, *, scope: str = "function", **kwargs):
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
                        # Expose dynamic values if available
                        self.dynamic_values = env.get("dynamic_values", {})
                        self.workflow_result = env.get(
                            "dynamic_values", {}
                        )  # Alias for backward compatibility

                    def __getitem__(self, key):
                        # Backward compatibility
                        return {
                            "nodes": self.nodes,
                            "endpoints": self.endpoints,
                            "manager": self.manager,
                            "workflow_result": self.success,
                            "dynamic_values": self.dynamic_values,
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

                    def get_captured_value(self, key, default=None):
                        """Get a captured dynamic value from the workflow execution"""
                        return self.dynamic_values.get(key, default)

                    def list_captured_values(self):
                        """List all captured dynamic values from the workflow execution"""
                        return list(self.dynamic_values.keys())

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


def _setup_near_devnet(
    contracts_dir: str | None, count: int, prefix: str
) -> tuple[Any, dict[str, Any]]:
    """
    Helper to spin up NEAR Sandbox, deploy contracts, and generate node accounts.
    Returns the sandbox instance and the configuration dictionary.
    """
    if not contracts_dir:
        raise ValueError("contracts_dir is required when near_devnet is True")

    from merobox.commands.near.sandbox import SandboxManager

    # Start Sandbox
    sandbox = SandboxManager()
    sandbox.start()

    # Paths to contracts
    ctx_path = os.path.join(contracts_dir, "calimero_context_config_near.wasm")
    proxy_path = os.path.join(contracts_dir, "calimero_context_proxy_near.wasm")

    configs = {}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Deploy Contracts
        contract_id = loop.run_until_complete(
            sandbox.setup_calimero(ctx_path, proxy_path)
        )

        # Get RPC URL
        rpc_url = sandbox.get_rpc_url(for_docker=True)

        # Generate configs for each node
        for i in range(count):
            node_name = f"{prefix}-{i+1}"
            creds = loop.run_until_complete(sandbox.create_node_account(node_name))
            configs[node_name] = {
                "rpc_url": rpc_url,
                "contract_id": contract_id,
                **creds,
            }
    except Exception:
        # Ensure sandbox is stopped if setup fails
        sandbox.stop_process()
        loop.close()
        raise
    finally:
        if not loop.is_closed():
            loop.close()

    return sandbox, configs
