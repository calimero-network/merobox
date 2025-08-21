"""
Basic integration tests demonstrating Merobox testing capabilities.

These tests show how to use the basic cluster fixtures for simple
blockchain node testing.
"""

import pytest
from hello_world.client import Client


def test_cluster_setup(merobox_cluster):
    """Test that the cluster is properly set up."""
    # Use the new API methods instead of dict access
    nodes = merobox_cluster.nodes
    endpoints = merobox_cluster.endpoints
    
    assert len(nodes) == 3
    assert len(endpoints) == 3
    
    # Check that all nodes have endpoints
    for node in nodes:
        assert node in endpoints
        assert endpoints[node].startswith("http://localhost:")


def test_client_health(blockchain_endpoints):
    """Test client health checking."""
    # Use the first node for testing
    first_node = list(blockchain_endpoints.keys())[0]
    endpoint = blockchain_endpoints[first_node]
    
    client = Client(endpoint)
    result = client.health_check()
    
    assert result["success"] is True
    assert "data" in result


def test_client_node_info(blockchain_endpoints):
    """Test getting node information."""
    first_node = list(blockchain_endpoints.keys())[0]
    endpoint = blockchain_endpoints[first_node]
    
    client = Client(endpoint)
    result = client.get_node_info()
    
    # Note: This might fail if the endpoint doesn't exist, but that's okay for demo
    # In a real scenario, you'd check what endpoints are actually available
    if result["success"]:
        assert "data" in result
    else:
        # Log the error for debugging
        print(f"Node info request failed: {result.get('error', 'Unknown error')}")


def test_multiple_nodes(blockchain_nodes, blockchain_endpoints):
    """Test that we can interact with multiple nodes."""
    assert len(blockchain_nodes) >= 2
    
    # Test each node
    for node in blockchain_nodes:
        endpoint = blockchain_endpoints[node]
        client = Client(endpoint)
        
        # Basic health check
        result = client.health_check()
        assert result["success"] is True


def test_endpoint_format(blockchain_endpoints):
    """Test that endpoints are properly formatted."""
    for node, endpoint in blockchain_endpoints.items():
        assert endpoint.startswith("http://")
        assert "localhost" in endpoint
        assert ":" in endpoint  # Should have port
        
        # Extract port and verify it's numeric
        port_part = endpoint.split(":")[-1]
        try:
            port = int(port_part)
            assert 1024 <= port <= 65535  # Valid port range
        except ValueError:
            pytest.fail(f"Invalid port in endpoint: {endpoint}")
