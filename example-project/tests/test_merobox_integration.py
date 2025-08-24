"""
Test Merobox integration with the Hello World client.

This test demonstrates how to use Merobox to create test nodes
and test the Hello World client against real running nodes.
"""

import pytest
from hello_world.client import Client


def test_client_creation(single_node):
    """Test the Hello World client creation."""
    # Get the endpoint for our test node
    endpoint = single_node.endpoint(0)
    
    # Create client connected to the test node
    client = Client(endpoint)
    
    # Test basic client functionality
    assert client.base_url == endpoint
    
    # Test that we can create a client instance
    assert isinstance(client, Client)


def test_client_methods_availability(single_node):
    """Test that the client has all expected methods."""
    endpoint = single_node.endpoint(0)
    client = Client(endpoint)
    
    # Verify all expected methods are available
    expected_methods = [
        "health_check",
        "get_node_info",
        "create_context",
        "list_contexts",
        "install_application"
    ]
    
    for method_name in expected_methods:
        assert hasattr(client, method_name), f"Method {method_name} not found"
        assert callable(getattr(client, method_name)), f"Method {method_name} is not callable"


def test_client_with_multiple_endpoints(endpoints):
    """Test the Hello World client with multiple endpoints."""
    # Test client creation for each endpoint
    for endpoint in endpoints:
        client = Client(endpoint)
        
        # Verify client is properly configured
        assert client.base_url == endpoint
        assert isinstance(client, Client)


def test_client_endpoint_consistency(single_node):
    """Test that node endpoints are consistent and accessible."""
    # Get endpoint by index
    endpoint_by_index = single_node.endpoint(0)
    
    # Get endpoint by name
    endpoint_by_name = single_node.endpoint(single_node.nodes[0])
    
    # Verify both methods return the same endpoint
    assert endpoint_by_index == endpoint_by_name
    
    # Verify endpoint format (should be an HTTP URL)
    assert endpoint_by_index.startswith("http://")
    assert ":" in endpoint_by_index  # Should have port


def test_client_instance_uniqueness(single_node):
    """Test that each client instance is unique."""
    endpoint1 = single_node.endpoint(0)
    endpoint2 = single_node.endpoint(0)  # Same endpoint, different instances
    
    client1 = Client(endpoint1)
    client2 = Client(endpoint2)
    
    # Verify clients are different instances
    assert client1 is not client2
    
    # But they should have the same base_url
    assert client1.base_url == client2.base_url


def test_client_attributes(single_node):
    """Test that client has the expected attributes."""
    endpoint = single_node.endpoint(0)
    client = Client(endpoint)
    
    # Test that base_url is set correctly
    assert hasattr(client, 'base_url')
    assert client.base_url == endpoint
    
    # Test that client is an instance of Client
    assert isinstance(client, Client)


def test_client_method_signatures(single_node):
    """Test that client methods have the expected signatures."""
    endpoint = single_node.endpoint(0)
    client = Client(endpoint)
    
    # Test that methods exist and are callable
    methods_to_test = [
        'health_check',
        'get_node_info',
        'create_context',
        'list_contexts',
        'install_application'
    ]
    
    for method_name in methods_to_test:
        method = getattr(client, method_name)
        assert callable(method), f"Method {method_name} is not callable"


def test_client_initialization(single_node):
    """Test client initialization with different parameters."""
    # Test basic initialization with node endpoint
    endpoint = single_node.endpoint(0)
    client1 = Client(endpoint)
    assert client1.base_url == endpoint
    
    # Test with different endpoint format (HTTPS)
    https_endpoint = endpoint.replace("http://", "https://")
    client2 = Client(https_endpoint)
    assert client2.base_url == https_endpoint


def test_client_equality(single_node):
    """Test client equality and comparison."""
    endpoint = single_node.endpoint(0)
    client1 = Client(endpoint)
    client2 = Client(endpoint)
    
    # Different instances should not be equal
    assert client1 is not client2
    
    # But they should have the same base_url
    assert client1.base_url == client2.base_url


def test_preconfigured_client(client):
    """Test the preconfigured client fixture."""
    # Verify the client fixture works
    assert isinstance(client, Client)
    assert hasattr(client, 'base_url')
    assert client.base_url.startswith("http://")


def test_multiple_nodes_access(single_node):
    """Test access to multiple nodes if available."""
    # Verify we can access the node
    assert single_node is not None
    
    # Verify we can get endpoints
    endpoint = single_node.endpoint(0)
    assert endpoint is not None
    assert isinstance(endpoint, str)
    
    # Verify endpoint is accessible
    assert endpoint.startswith("http://")


def test_client_with_node_manager(single_node):
    """Test access to the underlying node manager."""
    # Access the manager through the node
    manager = single_node.manager
    
    # Verify manager is available
    assert manager is not None
    
    # Verify we can get running nodes
    running_nodes = manager.get_running_nodes()
    assert isinstance(running_nodes, list)
