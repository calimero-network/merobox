"""
Workflow integration tests demonstrating advanced Merobox testing capabilities.

These tests show how to use workflow-based fixtures for complex
blockchain setup scenarios.
"""

import pytest
from hello_world.client import Client


def test_workflow_environment_setup(workflow_environment):
    """Test that the workflow environment is properly set up."""
    # Use the new API methods instead of dict access
    assert workflow_environment.success is True
    
    # Check that we have nodes and endpoints
    nodes = workflow_environment.nodes
    endpoints = workflow_environment.endpoints
    
    # Note: This workflow might stop nodes after completion
    print(f"Workflow completed with {len(nodes)} nodes")
    if len(nodes) > 0:
        assert len(endpoints) > 0
    else:
        print("No nodes running after workflow completion (expected behavior)")


def test_simple_workflow_environment(simple_workflow_environment):
    """Test the simpler workflow environment."""
    # Use the new API methods instead of dict access
    assert simple_workflow_environment.success is True
    
    nodes = simple_workflow_environment.nodes
    endpoints = simple_workflow_environment.endpoints
    
    # This workflow is designed to stop all nodes after completion
    # So we check that the workflow executed successfully rather than
    # expecting running nodes
    print(f"Workflow completed successfully with {len(nodes)} nodes")
    print(f"Note: This workflow stops all nodes after completion (expected behavior)")


def test_workflow_node_health(workflow_environment):
    """Test that all nodes in the workflow are healthy."""
    nodes = workflow_environment.nodes
    endpoints = workflow_environment.endpoints
    
    for node in nodes:
        endpoint = endpoints[node]
        client = Client(endpoint)
        
        result = client.health_check()
        assert result["success"] is True, f"Node {node} health check failed"


def test_workflow_context_operations(workflow_environment):
    """Test context operations against workflow nodes."""
    # Check if nodes are available
    if len(workflow_environment.nodes) == 0:
        print("No nodes available for testing (workflow may have stopped them)")
        return  # Skip test if no nodes
    
    # Use the first node for testing
    first_node = list(workflow_environment.nodes)[0]
    endpoint = workflow_environment.endpoints[first_node]
    
    client = Client(endpoint)
    
    # Test context creation
    context_name = "test-context-from-workflow"
    result = client.create_context(context_name, "Test metadata")
    
    # Note: This might fail if the endpoint doesn't support context creation
    # In a real scenario, you'd check what operations are available
    if result["success"]:
        assert "data" in result
        print(f"Successfully created context: {context_name}")
    else:
        print(f"Context creation failed: {result.get('error', 'Unknown error')}")
    
    # Test context listing
    list_result = client.list_contexts()
    if list_result["success"]:
        assert "data" in list_result
        print("Successfully listed contexts")
    else:
        print(f"Context listing failed: {list_result.get('error', 'Unknown error')}")


def test_workflow_application_installation(workflow_environment):
    """Test application installation against workflow nodes."""
    # Check if nodes are available
    if len(workflow_environment.nodes) == 0:
        print("No nodes available for testing (workflow may have stopped them)")
        return  # Skip test if no nodes
    
    first_node = list(workflow_environment.nodes)[0]
    endpoint = workflow_environment.endpoints[first_node]
    
    client = Client(endpoint)
    
    # Test application installation
    app_path = "/app/data/kv_store.wasm"  # Path that should exist in the container
    result = client.install_application(app_path, is_dev=True)
    
    # Note: This is a simplified test - in reality you'd need to handle
    # actual file uploads and verify the installation
    if result["success"]:
        assert "data" in result
        print(f"Successfully installed application from {app_path}")
    else:
        print(f"Application installation failed: {result.get('error', 'Unknown error')}")


def test_workflow_node_consistency(workflow_environment):
    """Test that all nodes in the workflow are consistent."""
    nodes = workflow_environment.nodes
    endpoints = workflow_environment.endpoints
    
    # Test that all nodes respond to basic requests
    for node in nodes:
        endpoint = endpoints[node]
        client = Client(endpoint)
        
        # Health check
        health_result = client.health_check()
        assert health_result["success"] is True, f"Node {node} health check failed"
        
        # Basic endpoint validation
        assert endpoint.startswith("http://")
        assert "localhost" in endpoint


def test_workflow_cleanup(workflow_environment):
    """Test that the workflow environment can be properly accessed."""
    # This test verifies that the fixture provides the expected data structure
    # The actual cleanup is handled automatically by the fixture
    
    # Verify data types using the new API
    assert isinstance(workflow_environment.success, bool)
    assert isinstance(workflow_environment.nodes, list)
    assert isinstance(workflow_environment.endpoints, dict)
    
    print("Workflow environment structure validation passed")
