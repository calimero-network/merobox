#!/bin/bash

# Example post-script that runs on all Calimero nodes after they have been started
# This script can be used for:
# - Health checks and validation
# - Configuration verification
# - Performance monitoring setup
# - Log analysis
# - Node-specific setup tasks

echo "🚀 Post-script execution started on $(hostname)"
echo "Node name: $NODE_NAME"
echo "Container ID: $(hostname)"
echo "Current user: $(whoami)"
echo "Working directory: $(pwd)"
echo "Timestamp: $(date)"

# Check if merod process is running
echo ""
echo "🔍 Checking merod process status..."
if pgrep -f merod > /dev/null; then
    echo "✅ merod process is running"
    echo "   Process info: $(ps aux | grep merod | grep -v grep | head -n1)"
else
    echo "❌ merod process not found"
fi

# Check network connectivity
echo ""
echo "🌐 Checking network connectivity..."
if command -v curl &> /dev/null; then
    echo "Testing HTTP connectivity..."
    if curl -s --max-time 5 http://httpbin.org/ip &> /dev/null; then
        echo "✅ HTTP connectivity successful"
    else
        echo "⚠️  HTTP connectivity failed"
    fi
else
    echo "⚠️  curl not available for network testing"
fi

# Check Calimero data directory
echo ""
echo "📁 Checking Calimero data directory..."
if [ -d "/app/data" ]; then
    echo "✅ /app/data directory exists"
    echo "   Contents: $(ls -la /app/data)"
    
    # Check for node-specific data
    if [ -n "$NODE_NAME" ] && [ -d "/app/data/$NODE_NAME" ]; then
        echo "✅ Node-specific data directory exists: /app/data/$NODE_NAME"
        echo "   Contents: $(ls -la /app/data/$NODE_NAME)"
    fi
else
    echo "❌ /app/data directory not found"
fi

# Check available ports
echo ""
echo "🔌 Checking available ports..."
if command -v netstat &> /dev/null; then
    echo "Listening ports:"
    netstat -tlnp 2>/dev/null | grep LISTEN || echo "   No listening ports found"
elif command -v ss &> /dev/null; then
    echo "Listening ports:"
    ss -tlnp 2>/dev/null | grep LISTEN || echo "   No listening ports found"
else
    echo "⚠️  netstat/ss not available for port checking"
fi

# Check system resources
echo ""
echo "💾 System resources on $(hostname):"
echo "Available disk space:"
df -h / 2>/dev/null || echo "   Could not check disk space"
echo ""
echo "Memory usage:"
free -h 2>/dev/null || echo "   Could not check memory"
echo ""
echo "Load average:"
cat /proc/loadavg 2>/dev/null || echo "   Could not check load average"

# Check Calimero logs if available
echo ""
echo "📋 Checking recent Calimero logs..."
if [ -d "/app/data" ]; then
    # Look for log files
    find /app/data -name "*.log" -type f 2>/dev/null | head -5 | while read log_file; do
        echo "   Found log file: $log_file"
        echo "   Last 3 lines:"
        tail -3 "$log_file" 2>/dev/null | sed 's/^/     /'
    done
else
    echo "   No data directory to search for logs"
fi

# Set node-specific environment variables
export NODE_READY="true"
export NODE_HOSTNAME="$(hostname)"
export NODE_TIMESTAMP="$(date +%s)"
export CALIMERO_HOME="/app/data"

echo ""
echo "🔧 Node-specific environment variables set:"
echo "   NODE_READY=$NODE_READY"
echo "   NODE_HOSTNAME=$NODE_HOSTNAME"
echo "   NODE_TIMESTAMP=$NODE_TIMESTAMP"
echo "   CALIMERO_HOME=$CALIMERO_HOME"

# Create node-specific directories
mkdir -p /tmp/node-$(hostname)
mkdir -p /tmp/calimero-status
echo "📁 Created node-specific directories: /tmp/node-$(hostname), /tmp/calimero-status"

# Write node status file
cat > /tmp/calimero-status/node-info.txt << EOF
Node Information
================
Hostname: $(hostname)
Node Name: ${NODE_NAME:-unknown}
Timestamp: $(date)
User: $(whoami)
Working Directory: $(pwd)
Calimero Home: ${CALIMERO_HOME:-unknown}
EOF

echo "📝 Created node status file: /tmp/calimero-status/node-info.txt"

# Test merod command if available
echo ""
echo "🧪 Testing merod command..."
if command -v merod &> /dev/null; then
    echo "✅ merod command available"
    echo "   Version: $(merod --version 2>/dev/null || echo 'unknown')"
    
    # Test merod help
    if merod --help &> /dev/null; then
        echo "   Help command successful"
    else
        echo "   Help command failed"
    fi
else
    echo "❌ merod command not found in PATH"
fi

echo ""
echo "✅ Post-script execution completed successfully on $(hostname)"
echo "   Node is ready for workflow execution"
