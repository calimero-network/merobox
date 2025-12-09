"""
Pytest configuration and fixtures for the Hello World example project.

This file demonstrates the new, cleaner Merobox testing API.
"""

import pytest
import os

from pathlib import Path
from merobox.testing import nodes, run_workflow

# Requires downloading the contracts, this is usually done in CI. For local development, match the path.
CONTRACTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../contracts/near"))


# ============================================================================
# Main session-scoped fixtures for reuse across all tests
# ============================================================================


@nodes(count=3, prefix="shared-test", scope="session", near_devnet=True, contracts_dir=CONTRACTS_DIR)
def shared_cluster():
    """Main shared cluster with 3 nodes for all tests - session scoped for maximum reuse"""
    pass


@nodes(count=2, prefix="multi-test", scope="function")
def multi_test_nodes():
    """Create multiple test nodes for cluster testing - function scoped for isolation"""
    pass


@run_workflow(
    "./workflows/workflow-example.yml", prefix="shared-workflow", scope="session", near_devnet=True, contracts_dir=CONTRACTS_DIR
)
def shared_workflow():
    """Shared workflow setup for advanced testing - session scoped for reuse"""
    pass


@pytest.fixture
def workflow_environment(shared_workflow):
    """Create a test environment using the shared workflow"""
    return shared_workflow


# ============================================================================
# Convenience fixtures that reuse the shared cluster
# ============================================================================


@pytest.fixture
def merobox_cluster(shared_cluster):
    """Alias for shared_cluster for backward compatibility"""
    return shared_cluster


@pytest.fixture
def two_nodes(shared_cluster):
    """Two nodes from the shared cluster"""
    return shared_cluster


@pytest.fixture
def cluster_a(shared_cluster):
    """Alias for shared_cluster to avoid port conflicts"""
    return shared_cluster


@pytest.fixture
def single_a(shared_cluster):
    """Single node from the shared cluster"""
    return shared_cluster


@pytest.fixture
def inline_node(shared_cluster):
    """Single node from the shared cluster"""
    return shared_cluster


@pytest.fixture
def dev_node(shared_cluster):
    """Single node from the shared cluster"""
    return shared_cluster


@pytest.fixture
def single_node(shared_cluster):
    """Single node from the shared cluster"""
    return shared_cluster


@pytest.fixture
def force_pull_setup(shared_workflow):
    """Alias for shared_workflow"""
    return shared_workflow


@pytest.fixture
def merobox_workflow(shared_workflow):
    """Alias for shared_workflow"""
    return shared_workflow


@pytest.fixture
def merobox_simple_workflow(shared_workflow):
    """Alias for shared_workflow"""
    return shared_workflow


@pytest.fixture
def simple_workflow_environment(shared_workflow):
    """Alias for shared_workflow"""
    return shared_workflow


# ============================================================================
# Backward compatibility fixtures
# ============================================================================


@pytest.fixture
def endpoints(shared_cluster):
    """Quick access to endpoints from the shared cluster"""
    return shared_cluster.endpoints


@pytest.fixture
def client(shared_cluster):
    """Quick access to client from the shared cluster"""
    from hello_world.client import Client

    return Client(shared_cluster.endpoint(0))


@pytest.fixture
def blockchain_nodes(shared_cluster):
    """Quick access to nodes from the shared cluster"""
    return shared_cluster.nodes


@pytest.fixture
def blockchain_endpoints(shared_cluster):
    """Quick access to endpoints from the shared cluster"""
    return shared_cluster.endpoints


@pytest.fixture
def blockchain_manager(shared_cluster):
    """Quick access to manager from the shared cluster"""
    return shared_cluster.manager


# ============================================================================
# Legacy fixtures for backward compatibility
# ============================================================================


@pytest.fixture
def merobox_nodes(shared_cluster):
    """Legacy fixture for backward compatibility"""
    return shared_cluster.nodes


@pytest.fixture
def merobox_endpoints(shared_cluster):
    """Legacy fixture for backward compatibility"""
    return shared_cluster.endpoints


@pytest.fixture
def merobox_manager(shared_cluster):
    """Legacy fixture for backward compatibility"""
    return shared_cluster.manager
