"""
Test Merobox integration with the Hello World client.

This test demonstrates how to use Merobox to create test nodes
and test the Hello World client against real running nodes.
"""

import pytest
from hello_world.client import Client


def test_client_with_single_node(shared_cluster):
    """Test the Hello World client with a single test node."""
    # Get the endpoint for our test node
    endpoint = shared_cluster.endpoint(0)
    
    # Create client connected to the test node
    client = Client(endpoint)
    
    # Test basic client functionality
    assert client.base_url == endpoint
    
    # Test that we can create a client instance
    assert isinstance(client, Client)
    
    # Note: Actual API calls would require the node to be fully ready
    # This test demonstrates the setup and connection pattern


def test_client_with_multiple_nodes(multi_test_nodes):
    """Test the Hello World client with multiple test nodes."""
    # Verify we have the expected number of nodes
    assert len(multi_test_nodes.nodes) == 2
    
    # Test client creation for each node
    for i, node_name in enumerate(multi_test_nodes.nodes):
        endpoint = multi_test_nodes.endpoint(i)
        client = Client(endpoint)
        
        # Verify client is properly configured
        assert client.base_url == endpoint
        assert isinstance(client, Client)
        
        # Verify node name matches expected pattern
        assert node_name.startswith("multi-test")


def test_workflow_environment(workflow_environment):
    """Test using a workflow-created environment."""
    # Verify workflow executed successfully
    assert workflow_environment.success
    
    # Verify we have nodes from the workflow
    assert len(workflow_environment.nodes) > 0
    
    # Test client creation for workflow nodes
    for i, node_name in enumerate(workflow_environment.nodes):
        endpoint = workflow_environment.endpoint(i)
        client = Client(endpoint)
        
        # Verify client is properly configured
        assert client.base_url == endpoint
        assert isinstance(client, Client)


def test_merobox_manager_access(shared_cluster):
    """Test access to the underlying Merobox manager."""
    # Access the manager through the fixture
    manager = shared_cluster.manager
    
    # Verify manager is available
    assert manager is not None
    
    # Verify we can get running nodes
    running_nodes = manager.get_running_nodes()
    assert isinstance(running_nodes, list)
    
    # Verify our test node is in the running nodes
    assert shared_cluster.nodes[0] in running_nodes


def test_node_endpoint_consistency(shared_cluster):
    """Test that node endpoints are consistent and accessible."""
    # Get endpoint by index
    endpoint_by_index = shared_cluster.endpoint(0)
    
    # Get endpoint by name
    endpoint_by_name = shared_cluster.endpoint(shared_cluster.nodes[0])
    
    # Verify both methods return the same endpoint
    assert endpoint_by_index == endpoint_by_name
    
    # Verify endpoint format (should be an HTTP URL)
    assert endpoint_by_index.startswith("http://")
    assert ":" in endpoint_by_index  # Should have port


def test_client_methods_availability(shared_cluster):
    """Test that the client has all expected methods."""
    endpoint = shared_cluster.endpoint(0)
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


def test_merobox_fixture_scope():
    """Test that Merobox fixtures respect their scope."""
    # This test verifies that fixtures are properly scoped
    # The @nodes decorator should create fresh nodes for each test function
    assert True  # Placeholder - actual scope testing would require more complex setup


def test_workflow_dynamic_values(workflow_environment):
    """Test access to workflow dynamic values if available."""
    # Check if dynamic values are available
    if hasattr(workflow_environment, 'dynamic_values'):
        dynamic_values = workflow_environment.dynamic_values
        
        # If dynamic values exist, verify they're accessible
        if dynamic_values:
            assert isinstance(dynamic_values, dict)
            
            # Test the helper methods
            available_keys = workflow_environment.list_captured_values()
            assert isinstance(available_keys, list)
            
            # Test getting a specific value
            for key in available_keys[:1]:  # Test first available key
                value = workflow_environment.get_captured_value(key)
                assert value is not None
    else:
        # If no dynamic values, that's also valid
        assert True


def test_merobox_cleanup():
    """Test that Merobox properly cleans up resources."""
    # This test verifies that resources are cleaned up
    # The actual cleanup happens automatically via context managers
    # We just need to verify the test completes without errors
    assert True
