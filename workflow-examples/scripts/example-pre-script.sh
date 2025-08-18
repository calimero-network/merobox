#!/bin/bash

# Example pre-script that runs on the Docker image before starting Calimero nodes
# This script can be used for:
# - Installing additional packages
# - Setting up environment variables
# - Configuring the container
# - Running health checks
# - Setting up networking

echo "🚀 Pre-script execution started"
echo "Container OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "Current user: $(whoami)"
echo "Working directory: $(pwd)"
echo "Available commands:"
which merod || echo "merod not found in PATH"
which curl || echo "curl not found in PATH"
which wget || echo "wget not found in PATH"

# Example: Install additional packages if needed
# apt-get update && apt-get install -y curl wget

# Example: Set environment variables
export CUSTOM_VAR="pre-script-value"
echo "Set CUSTOM_VAR=$CUSTOM_VAR"

# Example: Create directories
mkdir -p /tmp/pre-script-demo
echo "Created demo directory"

# Example: Check available disk space
echo "Available disk space:"
df -h /

# Example: Check memory
echo "Memory info:"
free -h

echo "✅ Pre-script execution completed successfully"
