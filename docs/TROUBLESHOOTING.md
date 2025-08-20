# Merobox Troubleshooting Guide

This guide helps you resolve common issues when using Merobox.

## Table of Contents

- [Common Issues](#common-issues)
- [Node Management Problems](#node-management-problems)
- [Workflow Execution Issues](#workflow-execution-issues)
- [Docker Problems](#docker-problems)
- [Network and Port Issues](#network-and-port-issues)
- [Performance Issues](#performance-issues)
- [Getting Help](#getting-help)

## Common Issues

### Command Not Found

**Problem**: `merobox: command not found`

**Solutions**:
1. **Check installation**:
   ```bash
   pip list | grep merobox
   ```

2. **Reinstall the package**:
   ```bash
   pip uninstall merobox
   pip install -e .
   ```

3. **Check PATH**:
   ```bash
   which merobox
   echo $PATH
   ```

4. **Use Python module syntax**:
   ```bash
   python -m merobox --help
   ```

### Import Errors

**Problem**: `ModuleNotFoundError` or `ImportError`

**Solutions**:
1. **Check virtual environment**:
   ```bash
   source venv/bin/activate  # or appropriate activation
   ```

2. **Reinstall dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Check Python version**:
   ```bash
   python --version
   # Ensure Python 3.8+
   ```

4. **Clear Python cache**:
   ```bash
   find . -name "*.pyc" -delete
   find . -name "__pycache__" -type d -exec rm -rf {} +
   ```

## Node Management Problems

### Nodes Won't Start

**Problem**: `merobox run` fails to start nodes

**Solutions**:
1. **Check Docker status**:
   ```bash
   docker --version
   docker ps
   docker info
   ```

2. **Check Docker permissions**:
   ```bash
   # Linux
   sudo usermod -aG docker $USER
   # Log out and back in
   
   # macOS/Windows: Ensure Docker Desktop is running
   ```

3. **Check port availability**:
   ```bash
   # Check if ports are in use
   netstat -tulpn | grep :2428
   netstat -tulpn | grep :2528
   
   # Use different ports
   merobox run --base-port 3000 --base-rpc-port 3100
   ```

4. **Check disk space**:
   ```bash
   df -h
   # Ensure sufficient space for Docker images and data
   ```

### Nodes Start But Are Unhealthy

**Problem**: Nodes start but `merobox health` shows failures

**Solutions**:
1. **Wait for startup**:
   ```bash
   # Nodes need time to initialize
   sleep 30
   merobox health
   ```

2. **Check node logs**:
   ```bash
   merobox logs calimero-node-1
   merobox logs calimero-node-2
   ```

3. **Check Docker container status**:
   ```bash
   docker ps -a
   docker logs <container-id>
   ```

4. **Restart nodes**:
   ```bash
   merobox stop --all
   sleep 5
   merobox run --count 2
   ```

### Can't Stop Nodes

**Problem**: `merobox stop` fails or nodes remain running

**Solutions**:
1. **Force stop with Docker**:
   ```bash
   docker stop $(docker ps -q --filter "name=calimero-node")
   ```

2. **Remove containers**:
   ```bash
   docker rm $(docker ps -aq --filter "name=calimero-node")
   ```

3. **Use nuke command**:
   ```bash
   merobox nuke --force
   ```

4. **Check for stuck processes**:
   ```bash
   ps aux | grep calimero
   # Kill if necessary
   kill -9 <process-id>
   ```

## Workflow Execution Issues

### Workflow Validation Fails

**Problem**: `merobox bootstrap validate` reports errors

**Solutions**:
1. **Check YAML syntax**:
   ```bash
   # Validate YAML syntax
   python -c "import yaml; yaml.safe_load(open('workflow.yml'))"
   ```

2. **Check required fields**:
   ```bash
   # Look for missing required fields in error messages
   merobox bootstrap validate workflow.yml -v
   ```

3. **Use sample as reference**:
   ```bash
   merobox bootstrap create-sample
   # Compare with your workflow
   ```

4. **Check step types**:
   ```bash
   # Ensure step types are valid
   # Valid types: install, context, identity, invite, join, call, wait, repeat, script
   ```

### Workflow Execution Fails

**Problem**: `merobox bootstrap run` fails during execution

**Solutions**:
1. **Check node health first**:
   ```bash
   merobox health
   # Ensure all nodes are healthy
   ```

2. **Run with verbose output**:
   ```bash
   merobox bootstrap run -v workflow.yml
   ```

3. **Check step-by-step**:
   ```bash
   # Test individual steps manually
   merobox install --node calimero-node-1 --path ./app.wasm
   ```

4. **Check variable resolution**:
   ```bash
   # Ensure all variables are properly exported
   # Check outputs configuration in each step
   ```

### Variable Resolution Issues

**Problem**: Variables like `{{app_id}}` are not resolved

**Solutions**:
1. **Check variable exports**:
   ```yaml
   # Ensure previous steps export variables
   outputs:
     app_id: "applicationId"
   ```

2. **Check variable names**:
   ```yaml
   # Variable names are case-sensitive
   # {{app_id}} ≠ {{AppId}}
   ```

3. **Check step order**:
   ```yaml
   # Variables must be exported before they're used
   # Install step must come before context step
   ```

4. **Use explicit variable names**:
   ```yaml
   # Avoid complex variable names
   outputs:
     simple_name: "complexFieldName"
   ```

## Docker Problems

### Docker Permission Denied

**Problem**: `permission denied` when running Docker commands

**Solutions**:
1. **Add user to docker group** (Linux):
   ```bash
   sudo usermod -aG docker $USER
   # Log out and back in
   ```

2. **Use sudo** (temporary):
   ```bash
   sudo docker ps
   ```

3. **Check Docker service**:
   ```bash
   sudo systemctl status docker
   sudo systemctl start docker
   ```

4. **Restart Docker Desktop** (macOS/Windows)

### Docker Image Pull Fails

**Problem**: Can't pull Calimero node image

**Solutions**:
1. **Check internet connection**:
   ```bash
   ping ghcr.io
   ```

2. **Check Docker registry access**:
   ```bash
   docker pull ghcr.io/calimero-network/node:latest
   ```

3. **Use alternative image**:
   ```bash
   merobox run --image alternative/calimero-node:latest
   ```

4. **Check Docker credentials**:
   ```bash
   docker login ghcr.io
   ```

### Container Resource Issues

**Problem**: Containers run out of memory or CPU

**Solutions**:
1. **Check system resources**:
   ```bash
   free -h
   top
   ```

2. **Limit container resources**:
   ```bash
   # Modify Docker run commands to limit resources
   docker run --memory=512m --cpus=1 ...
   ```

3. **Reduce node count**:
   ```bash
   merobox run --count 1  # Instead of 3+
   ```

4. **Check for resource leaks**:
   ```bash
   docker stats
   ```

## Network and Port Issues

### Port Already in Use

**Problem**: Ports 2428 or 2528 are already occupied

**Solutions**:
1. **Check what's using the ports**:
   ```bash
   netstat -tulpn | grep :2428
   lsof -i :2428
   ```

2. **Use different ports**:
   ```bash
   merobox run --base-port 3000 --base-rpc-port 3100
   ```

3. **Stop conflicting services**:
   ```bash
   # Stop services using the ports
   sudo systemctl stop <service-name>
   ```

4. **Check for existing Merobox instances**:
   ```bash
   docker ps | grep calimero
   # Stop if found
   ```

### Network Connectivity Issues

**Problem**: Nodes can't communicate with each other

**Solutions**:
1. **Check Docker network**:
   ```bash
   docker network ls
   docker network inspect bridge
   ```

2. **Check firewall settings**:
   ```bash
   # Linux
   sudo ufw status
   sudo iptables -L
   
   # macOS
   sudo pfctl -s all
   ```

3. **Use host networking** (advanced):
   ```bash
   # Modify Docker run commands to use host network
   docker run --network host ...
   ```

4. **Check DNS resolution**:
   ```bash
   nslookup calimero-node-1
   ```

## Performance Issues

### Slow Node Startup

**Problem**: Nodes take a long time to start

**Solutions**:
1. **Check Docker image size**:
   ```bash
   docker images ghcr.io/calimero-network/node
   ```

2. **Use SSD storage**:
   ```bash
   # Ensure Docker data directory is on SSD
   docker info | grep "Docker Root Dir"
   ```

3. **Optimize Docker settings**:
   ```bash
   # Increase Docker memory and CPU limits
   # In Docker Desktop settings
   ```

4. **Check system resources**:
   ```bash
   # Ensure sufficient RAM and CPU
   free -h
   nproc
   ```

### High Memory Usage

**Problem**: Merobox uses too much memory

**Solutions**:
1. **Monitor memory usage**:
   ```bash
   docker stats
   htop
   ```

2. **Limit container memory**:
   ```bash
   # Set memory limits in Docker run commands
   docker run --memory=512m ...
   ```

3. **Reduce node count**:
   ```bash
   merobox run --count 1  # Instead of multiple nodes
   ```

4. **Check for memory leaks**:
   ```bash
   # Monitor memory usage over time
   watch -n 1 'docker stats --no-stream'
   ```

### Slow Workflow Execution

**Problem**: Workflows take too long to complete

**Solutions**:
1. **Add appropriate wait times**:
   ```yaml
   - name: "Wait for operation"
     type: "wait"
     config:
       seconds: 10
   ```

2. **Check node health**:
   ```bash
   merobox health
   # Ensure nodes are responsive
   ```

3. **Optimize step order**:
   ```yaml
   # Run independent steps in parallel where possible
   # Use wait steps strategically
   ```

4. **Check for blocking operations**:
   ```yaml
   # Ensure async operations complete before next steps
   ```

## Getting Help

### Self-Diagnosis

1. **Check command help**:
   ```bash
   merobox --help
   merobox <command> --help
   ```

2. **Enable verbose output**:
   ```bash
   merobox <command> -v
   ```

3. **Check logs**:
   ```bash
   merobox logs <node-name>
   docker logs <container-id>
   ```

4. **Use dry-run mode**:
   ```bash
   merobox bootstrap run --dry-run workflow.yml
   ```

### Collecting Information

When reporting issues, include:

1. **System information**:
   ```bash
   uname -a
   python --version
   docker --version
   ```

2. **Merobox version**:
   ```bash
   merobox --version
   ```

3. **Error messages**: Copy complete error output
4. **Workflow files**: Share relevant YAML configurations
5. **Steps to reproduce**: Detailed reproduction steps

### Support Channels

- **GitHub Issues**: [Create an issue](https://github.com/calimero-network/merobox/issues)
- **Documentation**: Check this guide and other docs
- **Examples**: Review `workflow-examples/` directory
- **Community**: Check project discussions and forums

### Common Error Messages

| Error | Likely Cause | Solution |
|-------|--------------|----------|
| `No module named 'merobox'` | Package not installed | `pip install -e .` |
| `Permission denied` | Docker permissions | Add user to docker group |
| `Port already in use` | Port conflict | Use different ports |
| `Variable not found` | Missing export | Check step outputs |
| `Node not found` | Node not running | Start nodes first |
| `Connection refused` | Network issue | Check Docker network |

### Prevention Tips

1. **Always check node health** before running workflows
2. **Use verbose mode** for debugging
3. **Test workflows incrementally** - one step at a time
4. **Keep Docker updated** and running
5. **Monitor system resources** during operation
6. **Backup important data** before major operations

For more specific help, see the [Development Guide](DEVELOPMENT.md) and [API Reference](API_REFERENCE.md).
