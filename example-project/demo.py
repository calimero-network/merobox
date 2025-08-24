#!/usr/bin/env python3
"""
Demo script for the Hello World Example Project with Merobox Integration.

This script demonstrates how to use the Merobox package for managing
Calimero nodes and running workflows.
"""

import sys
import time
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from hello_world.client import Client

# Import Merobox functionality
try:
    from merobox.testing import CalimeroManager, cluster, workflow, nodes, run_workflow
    from merobox.commands.utils import get_node_rpc_url
    MEROBOX_AVAILABLE = True
except ImportError:
    MEROBOX_AVAILABLE = False
    print("⚠️  Merobox not available - some demos will be skipped")


def demo_merobox_installation():
    """Demonstrate that Merobox is properly installed."""
    print("=== Merobox Installation Demo ===")
    
    if MEROBOX_AVAILABLE:
        print("✅ Merobox package is successfully installed!")
        print(f"   Version: {getattr(sys.modules.get('merobox'), '__version__', 'Unknown')}")
        print(f"   Author: {getattr(sys.modules.get('merobox'), '__author__', 'Unknown')}")
    else:
        print("❌ Merobox package is not available")
        print("   Run: pip install -e .. from the example-project directory")
    
    print()


def demo_calimero_manager():
    """Demonstrate the CalimeroManager functionality."""
    if not MEROBOX_AVAILABLE:
        print("⚠️  Skipping CalimeroManager demo - Merobox not available")
        return
    
    print("=== CalimeroManager Demo ===")
    
    try:
        # Create a manager instance
        manager = CalimeroManager()
        print("✅ CalimeroManager initialized successfully")
        
        # Check Docker connection
        print(f"   Docker client: {type(manager.client).__name__}")
        print(f"   Current nodes: {len(manager.get_running_nodes())}")
        
        # Show available methods
        methods = [
            "run_node", "run_multiple_nodes", "stop_node", "stop_all_nodes",
            "get_running_nodes", "get_node_info", "force_pull_image"
        ]
        print(f"   Available methods: {', '.join(methods)}")
        
    except Exception as e:
        print(f"❌ Error initializing CalimeroManager: {e}")
        print("   Make sure Docker is running and accessible")
    
    print()


def demo_cluster_context_manager():
    """Demonstrate the cluster context manager."""
    if not MEROBOX_AVAILABLE:
        print("⚠️  Skipping cluster demo - Merobox not available")
        return
    
    print("=== Cluster Context Manager Demo ===")
    
    try:
        print("🚀 Starting a test cluster with 2 nodes...")
        
        # This would normally start actual nodes, but we'll just show the concept
        print("   Using: with cluster(count=2, prefix='demo') as env:")
        print("   - Creates 2 Calimero nodes")
        print("   - Automatically manages startup/shutdown")
        print("   - Provides node names and endpoints")
        print("   - Cleans up resources automatically")
        
        print("\n   Example usage:")
        print("   ```python")
        print("   @nodes(count=2, scope='function')")
        print("   def test_cluster():")
        print("       pass")
        print("   ")
        print("   def test_something(test_cluster):")
        print("       nodes = test_cluster.nodes")
        print("       endpoints = test_cluster.endpoints")
        print("       # Test with real nodes...")
        print("   ```")
        
    except Exception as e:
        print(f"❌ Error in cluster demo: {e}")
    
    print()


def demo_workflow_execution():
    """Demonstrate workflow execution capabilities."""
    if not MEROBOX_AVAILABLE:
        print("⚠️  Skipping workflow demo - Merobox not available")
        return
    
    print("=== Workflow Execution Demo ===")
    
    try:
        print("🔄 Merobox can execute complex workflows:")
        print("   - Multi-step node setup")
        print("   - Application installation")
        print("   - Configuration management")
        print("   - Dynamic value capture")
        
        print("\n   Example usage:")
        print("   ```python")
        print("   @run_workflow('workflow-example.yml', scope='session')")
        print("   def test_setup():")
        print("       pass")
        print("   ")
        print("   def test_something(test_setup):")
        print("       assert test_setup.success")
        print("       nodes = test_setup.nodes")
        print("       values = test_setup.dynamic_values")
        print("   ```")
        
        # Check if workflow examples exist
        workflow_dir = Path(__file__).parent.parent / "workflow-examples"
        if workflow_dir.exists():
            workflows = list(workflow_dir.glob("*.yml"))
            print(f"\n   📁 Available workflow examples: {len(workflows)}")
            for wf in workflows[:3]:  # Show first 3
                print(f"      - {wf.name}")
            if len(workflows) > 3:
                print(f"      ... and {len(workflows) - 3} more")
        
    except Exception as e:
        print(f"❌ Error in workflow demo: {e}")
    
    print()


def demo_hello_world_client():
    """Demonstrate the Hello World client functionality."""
    print("=== Hello World Client Demo ===")
    
    # Create a client (this would normally connect to a real node)
    client = Client("http://localhost:2528")
    
    print(f"✅ Client initialized with base URL: {client.base_url}")
    
    # Show available methods
    methods = [
        "health_check",
        "get_node_info", 
        "create_context",
        "list_contexts",
        "install_application"
    ]
    
    print(f"📋 Available methods: {', '.join(methods)}")
    
    # Note: These calls would fail without a running node
    print("\n⚠️  Note: These methods require a running Calimero node to work properly.")
    print("   Use the Merobox testing framework to create test nodes.")
    
    print()


def demo_integration_example():
    """Show how to integrate Merobox with the Hello World client."""
    if not MEROBOX_AVAILABLE:
        print("⚠️  Skipping integration demo - Merobox not available")
        return
    
    print("=== Integration Example Demo ===")
    
    print("🔗 Here's how to use Merobox with the Hello World client:")
    print("\n   ```python")
    print("   import pytest")
    print("   from merobox.testing import nodes")
    print("   from hello_world.client import Client")
    print("   ")
    print("   @nodes(count=1, scope='function')")
    print("   def test_node():")
    print("       pass")
    print("   ")
    print("   def test_client_health(test_node):")
    print("       # Get the endpoint for our test node")
    print("       endpoint = test_node.endpoint(0)")
    print("       ")
    print("       # Create client connected to the test node")
    print("       client = Client(endpoint)")
    print("       ")
    print("       # Now test with a real running node!")
    print("       result = client.health_check()")
    print("       assert result is not None")
    print("   ```")
    
    print()


def demo_project_structure():
    """Show the project structure."""
    print("=== Project Structure ===")
    
    project_root = Path(__file__).parent
    print(f"📁 Project root: {project_root}")
    
    print("\n🔑 Key files and directories:")
    print(f"  📁 src/hello_world/     - Main package")
    print(f"  📁 tests/               - Test suite")
    print(f"  📄 conftest.py          - Pytest fixtures")
    print(f"  📄 pyproject.toml       - Project configuration")
    print(f"  📄 README.md            - Project documentation")
    print(f"  📄 demo.py              - This demo script")
    
    if MEROBOX_AVAILABLE:
        print(f"  📦 merobox/            - Merobox package (installed)")
    
    print()


def demo_testing_approach():
    """Explain the testing approach."""
    print("=== Testing Approach ===")
    
    print("This project demonstrates how to use Merobox for testing:")
    print("\n1. 🚀 **Basic Cluster Testing**")
    print("   - Simple node setup and teardown")
    print("   - Basic health checking and validation")
    print("   - Automatic resource management")
    
    print("\n2. 🔄 **Workflow-based Testing**")
    print("   - Complex Calimero setup scenarios")
    print("   - Automatic workflow execution")
    print("   - Multi-step test preparation")
    print("   - Dynamic value capture")
    
    print("\n3. 🧹 **Automatic Resource Management**")
    print("   - Docker containers automatically cleaned up")
    print("   - No resource leaks in tests")
    print("   - Consistent test environments")
    
    print("\n4. 🔧 **Merobox Integration**")
    print("   - Direct package usage in tests")
    print("   - Context managers for easy setup")
    print("   - Pytest fixtures for clean testing")
    
    print()


def main():
    """Run the demo."""
    print("🚀 Hello World Example Project with Merobox Integration")
    print("=" * 60)
    
    demo_merobox_installation()
    demo_project_structure()
    demo_calimero_manager()
    demo_cluster_context_manager()
    demo_workflow_execution()
    demo_hello_world_client()
    demo_integration_example()
    demo_testing_approach()
    
    print("=" * 60)
    print("🎯 To run the actual tests:")
    print("   cd example-project")
    print("   python -m pytest -v")
    print("\n📚 See README.md for detailed usage instructions")
    
    if MEROBOX_AVAILABLE:
        print("\n✨ Merobox is ready to use!")
        print("   Try running the tests to see it in action.")
    else:
        print("\n⚠️  Install Merobox first:")
        print("   pip install -e ..")


if __name__ == "__main__":
    main()
