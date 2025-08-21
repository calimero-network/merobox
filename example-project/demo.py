#!/usr/bin/env python3
"""
Demo script for the Hello World Example Project.

This script demonstrates how to use the project's components
without running the full test suite.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from hello_world.client import Client


def demo_client():
    """Demonstrate the client functionality."""
    print("=== Client Demo ===")
    
    # Create a client (this would normally connect to a real node)
    client = Client("http://localhost:2528")
    
    print(f"Client initialized with base URL: {client.base_url}")
    
    # Show available methods
    methods = [
        "health_check",
        "get_node_info", 
        "create_context",
        "list_contexts",
        "install_application"
    ]
    
    print(f"Available methods: {', '.join(methods)}")
    
    # Note: These calls would fail without a running node
    print("\nNote: These methods require a running Calimero node to work properly.")
    print("Use the test suite to see them in action with real nodes.")


def demo_project_structure():
    """Show the project structure."""
    print("\n=== Project Structure ===")
    
    project_root = Path(__file__).parent
    print(f"Project root: {project_root}")
    
    print("\nKey files and directories:")
    print(f"  📁 src/hello_world/     - Main package")
    print(f"  📁 tests/               - Test suite")
    print(f"  📄 conftest.py          - Pytest fixtures")
    print(f"  📄 pyproject.toml       - Project configuration")
    print(f"  📄 README.md            - Project documentation")
    print(f"  📄 demo.py              - This demo script")


def demo_testing_approach():
    """Explain the testing approach."""
    print("\n=== Testing Approach ===")
    
    print("This project demonstrates how to use Merobox for testing:")
    print("\n1. 🚀 **Basic Cluster Testing**")
    print("   - Simple node setup and teardown")
    print("   - Basic health checking and validation")
    
    print("\n2. 🔄 **Workflow-based Testing**")
    print("   - Complex Calimero setup scenarios")
    print("   - Automatic workflow execution")
    print("   - Multi-step test preparation")
    
    print("\n3. 🧹 **Automatic Resource Management**")
    print("   - Docker containers automatically cleaned up")
    print("   - No resource leaks in tests")
    print("   - Consistent test environments")


def main():
    """Run the demo."""
    print("🚀 Hello World Example Project Demo")
    print("=" * 50)
    
    demo_project_structure()
            demo_client()
    demo_testing_approach()
    
    print("\n" + "=" * 50)
    print("🎯 To run the actual tests:")
    print("   cd example-project")
    print("   python -m pytest -v")
    print("\n📚 See README.md for detailed usage instructions")


if __name__ == "__main__":
    main()
