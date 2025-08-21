"""
Pytest configuration and fixtures for the Hello World example project.

This file demonstrates the new, cleaner Merobox testing API.
"""

import pytest
from pathlib import Path
from merobox.testing import nodes, run_workflow
from hello_world.client import Client


# ============================================================================
# Clean, decorator-based fixtures using the new API
# ============================================================================

# ============================================================================
# Main session-scoped fixtures for reuse across all tests
# ============================================================================

@nodes(count=3, prefix="shared-test", scope="session")
def shared_cluster():
    """Main shared cluster with 3 nodes for all tests - session scoped for maximum reuse"""
    pass


@run_workflow("test-workflow.yml", prefix="shared-workflow", scope="session")
def shared_workflow():
    """Shared workflow setup for advanced testing - session scoped for reuse"""
    pass


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
def workflow_environment(shared_workflow):
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
    """Pre-configured client for the first node in the shared cluster"""
    first_endpoint = shared_cluster.endpoint(0)
    return Client(first_endpoint)


@pytest.fixture
def blockchain_endpoints(shared_cluster):
    """Provide endpoints for testing."""
    return shared_cluster.endpoints


@pytest.fixture
def blockchain_nodes(shared_cluster):
    """Provide node names for testing."""
    return shared_cluster.nodes
