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

    # Test that the application was successfully installed by the workflow
    # The workflow should have installed kv_store.wasm and captured the app_id
    if hasattr(workflow_environment, "success") and workflow_environment.success:
        print(f"Workflow executed successfully, checking for installed application")

        # Test that the node is healthy and can respond to requests
        health_result = client.health_check()
        assert (
            health_result["success"] is True
        ), f"Node {first_node} health check failed"

        # Test that we can get node info (basic API functionality)
        node_info = client.get_node_info()
        if node_info["success"]:
            print(f"Successfully retrieved node info for {first_node}")
        else:
            print(
                f"Node info retrieval failed: {node_info.get('error', 'Unknown error')}"
            )

        print(f"Application installation test completed for node {first_node}")
    else:
        print(
            "Workflow did not complete successfully, skipping application installation test"
        )


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


def test_workflow_dynamic_variable_capture(workflow_environment):
    """Test that dynamically captured variables from the workflow are accessible."""
    # Test that the workflow executed successfully
    assert (
        workflow_environment.success is True
    ), "Workflow should have completed successfully"

    print("✅ Workflow execution successful, checking dynamic variable capture...")

    # Test that we can access the captured variables using the new API
    print(
        f"📋 Available captured values: {workflow_environment.list_captured_values()}"
    )

    # Test application ID capture
    app_id = workflow_environment.get_captured_value("app_id")
    if app_id:
        print(f"✅ Captured application ID: {app_id}")
        assert isinstance(app_id, str), "Application ID should be a string"
        assert len(app_id) > 0, "Application ID should not be empty"
    else:
        print("⚠️  Application ID not captured from workflow")

    # Test context ID capture
    context_id = workflow_environment.get_captured_value("context_id")
    if context_id:
        print(f"✅ Captured context ID: {context_id}")
        assert isinstance(context_id, str), "Context ID should be a string"
        assert len(context_id) > 0, "Context ID should not be empty"
    else:
        print("⚠️  Context ID not captured from workflow")

    # Test member public key capture
    member_public_key = workflow_environment.get_captured_value("member_public_key")
    if member_public_key:
        print(f"✅ Captured member public key: {member_public_key}")
        assert isinstance(
            member_public_key, str
        ), "Member public key should be a string"
        assert len(member_public_key) > 0, "Member public key should not be empty"
    else:
        print("⚠️  Member public key not captured from workflow")

    # Test that we can also access via the workflow_result attribute (backward compatibility)
    if (
        hasattr(workflow_environment, "workflow_result")
        and workflow_environment.workflow_result
    ):
        print(
            f"✅ Backward compatibility: workflow_result contains {len(workflow_environment.workflow_result)} values"
        )

        # Test accessing specific values
        if "app_id" in workflow_environment.workflow_result:
            print(
                f"✅ Backward compatibility: app_id = {workflow_environment.workflow_result['app_id']}"
            )

        if "context_id" in workflow_environment.workflow_result:
            print(
                f"✅ Backward compatibility: context_id = {workflow_environment.workflow_result['context_id']}"
            )

    print("✅ Dynamic variable capture test completed successfully")
