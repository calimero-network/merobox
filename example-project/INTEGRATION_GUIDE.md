# Merobox Integration Guide

This document explains how the Hello World Example Project demonstrates **Merobox** integration as a testing framework for Calimero applications.

## 🎯 What This Example Shows

The example project demonstrates how to use Merobox in a real-world scenario where you have:

1. **Your Own Application Code** (`src/hello_world/`)
2. **Test Suite** (`tests/`)
3. **Merobox Integration** (`conftest.py`)

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Project                            │
├─────────────────────────────────────────────────────────────┤
│  src/hello_world/                                          │
│  ├── client.py               ← Your client                │
│  └── __init__.py                                            │
├─────────────────────────────────────────────────────────────┤
│  tests/                                                     │
│  ├── test_basic_integration.py    ← Tests using Merobox    │
│  └── test_workflow_integration.py ← Workflow-based tests   │
├─────────────────────────────────────────────────────────────┤
│  conftest.py                ← Merobox fixtures             │
│  pyproject.toml             ← Project configuration        │
└─────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Merobox Framework                       │
│  ├── Cluster Management     ← Docker node lifecycle        │
│  ├── Workflow Execution     ← YAML-based setup             │
│  ├── Health Checking        ← Node readiness validation    │
│  └── Automatic Cleanup      ← Resource management          │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 Key Integration Points

### 1. **Dependencies** (`pyproject.toml`)

```toml
[project]
dependencies = [
    "merobox",        # ← Merobox testing framework
    "requests",       # ← HTTP client for your app
    "pytest",         # ← Test runner
]
```

### 2. **Fixtures** (`conftest.py`)

```python
from merobox.testing import pytest_cluster, pytest_workflow

# Basic cluster fixture
merobox_cluster = pytest_cluster(
    count=2,
    prefix="hello-test",
    scope="session"
)

# Workflow-based fixture
merobox_workflow = pytest_workflow(
    workflow_path="../workflow-examples/workflow-example.yml",
    prefix="hello-workflow",
    scope="session"
)
```

### 3. **Test Usage** (`tests/test_*.py`)

```python
def test_my_application(merobox_cluster):
    # Merobox automatically provides:
    nodes = merobox_cluster["nodes"]           # ← Node names
    endpoints = merobox_cluster["endpoints"]   # ← RPC URLs
    manager = merobox_cluster["manager"]       # ← Docker manager
    
    # Your test logic here
    for node in nodes:
        endpoint = endpoints[node]
        # Test against the node
```

## 🚀 How It Works

### **Before Tests**
1. Merobox starts Docker containers for Calimero nodes
2. Waits for nodes to be healthy and ready
3. Provides endpoints and node information to tests

### **During Tests**
1. Your tests receive the configured environment
2. Tests can interact with real Calimero nodes
3. All operations use actual Calimero infrastructure

### **After Tests**
1. Merobox automatically stops all containers
2. Cleans up Docker resources
3. Ensures no resource leaks

## 💡 Real-World Usage Patterns

### **Pattern 1: Simple Node Testing**
```python
def test_basic_functionality(merobox_cluster):
    """Test basic Calimero operations."""
    endpoint = merobox_cluster["endpoints"]["node-1"]
    client = Client(endpoint)
    
    result = client.health_check()
    assert result["success"] is True
```

### **Pattern 2: Workflow-based Testing**
```python
def test_complex_scenario(workflow_environment):
    """Test against a complex Calimero setup."""
    # Workflow has already:
    # - Started nodes
    # - Installed applications
    # - Created contexts
    # - Set up identities
    
    # Your tests can now verify the setup
    assert workflow_environment["workflow_result"] is True
    
    # Test your application against the prepared environment
    # ...
```

### **Pattern 3: Multi-Node Testing**
```python
def test_node_consistency(merobox_cluster):
    """Test consistency across multiple nodes."""
    nodes = merobox_cluster["nodes"]
    endpoints = merobox_cluster["endpoints"]
    
    # Test that all nodes are consistent
    for node in nodes:
        endpoint = endpoints[node]
        client = Client(endpoint)
        
        # Verify consistent behavior
        result = client.health_check()
        assert result["success"] is True
```

## 🔄 Workflow Integration

### **What Workflows Provide**
- **Complex Setup**: Multi-step Calimero configuration
- **Application Installation**: Deploy your apps to nodes
- **Context Creation**: Set up Calimero contexts
- **Identity Management**: Configure cryptographic identities
- **State Preparation**: Get nodes into specific states

### **Workflow Example**
```yaml
name: "Test Setup Workflow"
force_pull_image: true
nodes: ["test-node-1", "test-node-2"]
steps:
  - type: "install"
    node: "test-node-1"
    path: "my-app.wasm"
    dev: true
  - type: "context"
    action: "create"
    name: "test-context"
  - type: "identity"
    action: "generate"
    name: "test-identity"
```

## 🧹 Resource Management

### **Automatic Cleanup**
- Docker containers are automatically stopped
- Ports are automatically freed
- Data directories are cleaned up
- No resource leaks between test runs

### **Fixture Scopes**
- **`function`**: Fresh setup for each test (isolated)
- **`class`**: Shared setup for test class
- **`module`**: Shared setup for test module
- **`session`**: Shared setup for entire test session (fastest)

## 🚨 Best Practices

### **1. Use Appropriate Fixture Scopes**
```python
# For isolated tests
merobox_cluster = pytest_cluster(scope="function")

# For performance-critical tests
merobox_cluster = pytest_cluster(scope="session")
```

### **2. Handle Workflow Failures**
```python
def test_workflow_setup(workflow_environment):
    # Always check workflow success
    assert workflow_environment["workflow_result"] is True
    
    # Verify expected resources exist
    assert len(workflow_environment["nodes"]) > 0
```

### **3. Use Meaningful Node Prefixes**
```python
merobox_cluster = pytest_cluster(
    prefix="my-app-test",  # ← Clear identification
    count=2
)
```

### **4. Test Against Multiple Nodes**
```python
def test_multi_node_behavior(merobox_cluster):
    nodes = merobox_cluster["nodes"]
    endpoints = merobox_cluster["endpoints"]
    
    # Test consistency across all nodes
    for node in nodes:
        # Your test logic
        pass
```

## 🔗 Integration Steps for Your Project

### **Step 1: Install Merobox**
```bash
pip install merobox
```

### **Step 2: Create conftest.py**
```python
from merobox.testing import pytest_cluster

# Define your fixtures
my_cluster = pytest_cluster(count=1, prefix="my-app")
```

### **Step 3: Write Tests**
```python
def test_my_app(my_cluster):
    # Use the fixture
    endpoints = my_cluster["endpoints"]
    # Your test logic
```

### **Step 4: Create Workflows (Optional)**
```yaml
# my-test-workflow.yml
name: "My Test Setup"
nodes: ["my-test-node"]
steps:
  - type: "install"
    node: "my-test-node"
    path: "my-app.wasm"
    dev: true
```

## 📚 Next Steps

1. **Run the Example**: `cd example-project && python -m pytest -v`
2. **Modify the Code**: Adapt to your Calimero application
3. **Create Workflows**: Design workflows for your test scenarios
4. **Scale Up**: Add more complex testing scenarios

## 🤝 Support

- **Merobox Documentation**: See the main README.md
- **Issues**: Report problems to the Merobox repository
- **Examples**: Check the testing-examples/ directory
- **Workflows**: Explore workflow-examples/ directory
