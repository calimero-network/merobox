# Node Management Flags

This document explains how the node management flags work in Merobox workflows.

## 🚀 **Flag Overview**

Merobox now has two flags that control node management behavior:

- **`restart`** → Controls node restart at the **beginning** of the workflow
- **`stop_all_nodes`** → Controls node stopping at the **end** of the workflow

## 🔄 **How They Work**

### **Workflow Execution Flow**

```
1. Workflow Starts
   ↓
2. Check restart flag
   ├── restart: true  → Stop and restart workflow nodes
   └── restart: false → Check if nodes are running, reuse if possible
   ↓
3. Execute workflow steps
   ↓
4. Check stop_all_nodes flag
   ├── stop_all_nodes: true  → Stop all nodes on the system
   └── stop_all_nodes: false → Leave nodes running
   ↓
5. Workflow Complete
```

## 📋 **Flag Combinations**

### **1. Fresh Start Workflow**
```yaml
stop_all_nodes: true
restart: true
```
**Behavior:**
- **Beginning**: Restart workflow nodes (fresh state)
- **End**: Stop all nodes on the system
- **Use Case**: Complete system reset, testing from scratch

### **2. Efficient Rerun Workflow**
```yaml
stop_all_nodes: false
restart: false
```
**Behavior:**
- **Beginning**: Reuse existing nodes if running
- **End**: Leave nodes running for future workflows
- **Use Case**: Development iteration, quick testing

### **3. Restart but Keep Running**
```yaml
stop_all_nodes: false
restart: true
```
**Behavior:**
- **Beginning**: Restart workflow nodes (fresh state)
- **End**: Leave nodes running for future workflows
- **Use Case**: Clean state for this workflow, but preserve for others

### **4. Full Cleanup Workflow**
```yaml
stop_all_nodes: true
restart: true
```
**Behavior:**
- **Beginning**: Restart workflow nodes (fresh state)
- **End**: Stop all nodes on the system
- **Use Case**: Nuclear option, complete cleanup

## 💡 **Use Cases & Examples**

### **Development Workflow**
```yaml
name: Development Iteration
stop_all_nodes: false  # Don't stop at end
restart: false          # Don't restart at beginning
```
**Why**: Fast iteration, preserve node state between runs

### **Testing Workflow**
```yaml
name: Clean Test Run
stop_all_nodes: false  # Don't stop at end
restart: true           # Restart at beginning for clean state
```
**Why**: Fresh state for testing, but don't affect other workflows

### **Production Workflow**
```yaml
name: Production Deployment
stop_all_nodes: true   # Stop all at end
restart: true           # Restart at beginning
```
**Why**: Complete control, clean environment

### **Shared Environment Workflow**
```yaml
name: Shared Testing
stop_all_nodes: false  # Don't stop at end
restart: false          # Don't restart at beginning
```
**Why**: Work with existing nodes, don't disrupt others

## 🔍 **What Happens in Each Step**

### **Step 1: Node Management (Beginning)**
```
restart: true
├── Stop workflow nodes
├── Remove containers
├── Start fresh nodes
└── Initialize data

restart: false
├── Check if nodes are running
├── Reuse if running
├── Start if not running
└── Preserve existing state
```

### **Step 5: Node Cleanup (End)**
```
stop_all_nodes: true
├── Stop ALL nodes on system
├── Remove ALL containers
└── Clean slate

stop_all_nodes: false
├── Leave nodes running
├── Preserve state
└── Ready for next workflow
```

## ⚠️ **Important Notes**

1. **`stop_all_nodes` takes precedence**: If `stop_all_nodes: true`, it will stop ALL nodes regardless of other settings

2. **System-wide impact**: `stop_all_nodes: true` affects ALL Calimero nodes on your system, not just workflow nodes

3. **Port conflicts**: Be careful with `restart: false` if you have multiple workflows using the same ports

4. **State preservation**: `restart: false` preserves node state, which may not always be desired

## 🎯 **Best Practices**

### **For Development**
```yaml
stop_all_nodes: false
restart: false
```
- Fast iteration
- Preserve state
- Efficient development cycle

### **For Testing**
```yaml
stop_all_nodes: false
restart: true
```
- Clean state
- Isolated testing
- Don't affect other workflows

### **For Production**
```yaml
stop_all_nodes: true
restart: true
```
- Complete control
- Clean environment
- Predictable state

### **For Shared Environments**
```yaml
stop_all_nodes: false
restart: false
```
- Work with existing nodes
- Don't disrupt others
- Collaborative development

## 🔧 **Migration from Old Behavior**

The old behavior was:
- `stop_all_nodes: true` → Stop all nodes at beginning
- No restart flag → Always restart nodes

The new behavior is:
- `restart: true` → Restart nodes at beginning
- `stop_all_nodes: true` → Stop all nodes at end

**To maintain old behavior**, use:
```yaml
stop_all_nodes: true
restart: true
```

**For new efficient behavior**, use:
```yaml
stop_all_nodes: false
restart: false
```
