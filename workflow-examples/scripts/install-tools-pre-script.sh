#!/bin/bash

# Enhanced pre-script that installs additional tools (curl and perf)
# This script demonstrates the ability to install packages before starting Calimero nodes

echo "🚀 Enhanced Pre-script with Package Installation Started"
echo "Container OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "Current user: $(whoami)"
echo "Working directory: $(pwd)"

# Check what package manager is available
if command -v apt-get &> /dev/null; then
    echo "📦 Using apt-get package manager"
    PACKAGE_MANAGER="apt-get"
    UPDATE_CMD="apt-get update"
    INSTALL_CMD="apt-get install -y"
elif command -v yum &> /dev/null; then
    echo "📦 Using yum package manager"
    PACKAGE_MANAGER="yum"
    UPDATE_CMD="yum update -y"
    INSTALL_CMD="yum install -y"
elif command -v apk &> /dev/null; then
    echo "📦 Using apk package manager"
    PACKAGE_MANAGER="apk"
    UPDATE_CMD="apk update"
    INSTALL_CMD="apk add"
else
    echo "⚠️  No supported package manager found"
    PACKAGE_MANAGER="none"
fi

# Install curl if not available
if ! command -v curl &> /dev/null; then
    echo "📥 Installing curl..."
    if [ "$PACKAGE_MANAGER" != "none" ]; then
        eval $UPDATE_CMD
        eval $INSTALL_CMD curl
        if command -v curl &> /dev/null; then
            echo "✅ curl installed successfully"
            echo "   curl version: $(curl --version | head -n1)"
        else
            echo "❌ Failed to install curl"
        fi
    else
        echo "⚠️  Cannot install curl - no package manager available"
    fi
else
    echo "✅ curl already available: $(curl --version | head -n1)"
fi

# Install perf if not available
if ! command -v perf &> /dev/null; then
    echo "📥 Installing perf..."
    if [ "$PACKAGE_MANAGER" = "apt-get" ]; then
        eval $UPDATE_CMD
        eval $INSTALL_CMD linux-tools-common linux-tools-generic
        if command -v perf &> /dev/null; then
            echo "✅ perf installed successfully"
            echo "   perf version: $(perf --version | head -n1)"
        else
            echo "❌ Failed to install perf"
        fi
    elif [ "$PACKAGE_MANAGER" = "yum" ]; then
        eval $UPDATE_CMD
        eval $INSTALL_CMD perf
        if command -v perf &> /dev/null; then
            echo "✅ perf installed successfully"
            echo "   perf version: $(perf --version | head -n1)"
        else
            echo "❌ Failed to install perf"
        fi
    elif [ "$PACKAGE_MANAGER" = "apk" ]; then
        eval $UPDATE_CMD
        eval $INSTALL_CMD perf
        if command -v perf &> /dev/null; then
            echo "✅ perf installed successfully"
            echo "   perf version: $(perf --version | head -n1)"
        else
            echo "❌ Failed to install perf"
        fi
    else
        echo "⚠️  Cannot install perf - no package manager available"
    fi
else
    echo "✅ perf already available: $(perf --version | head -n1)"
fi

# Test the installed tools
echo ""
echo "🧪 Testing installed tools..."

if command -v curl &> /dev/null; then
    echo "✅ curl test: $(curl --version | head -n1)"
    # Test basic HTTP request
    echo "   Testing HTTP request to httpbin.org..."
    if curl -s --max-time 5 http://httpbin.org/ip &> /dev/null; then
        echo "   ✅ HTTP request successful"
    else
        echo "   ⚠️  HTTP request failed (may be network issue)"
    fi
fi

if command -v perf &> /dev/null; then
    echo "✅ perf test: $(perf --version | head -n1)"
    # Test perf list (basic functionality)
    if perf list &> /dev/null; then
        echo "   ✅ perf list command successful"
    else
        echo "   ⚠️  perf list command failed (may need elevated privileges)"
    fi
fi

# Show available commands after installation
echo ""
echo "🔍 Available commands after installation:"
which merod || echo "merod not found in PATH"
which curl || echo "curl not found in PATH"
which perf || echo "perf not found in PATH"
which wget || echo "wget not found in PATH"
which jq || echo "jq not found in PATH"

# Set environment variables
export TOOLS_INSTALLED="true"
export CURL_AVAILABLE="$(command -v curl &> /dev/null && echo 'yes' || echo 'no')"
export PERF_AVAILABLE="$(command -v perf &> /dev/null && echo 'yes' || echo 'no')"
echo ""
echo "🔧 Environment variables set:"
echo "   TOOLS_INSTALLED=$TOOLS_INSTALLED"
echo "   CURL_AVAILABLE=$CURL_AVAILABLE"
echo "   PERF_AVAILABLE=$PERF_AVAILABLE"

# Create demo directories
mkdir -p /tmp/tools-demo
mkdir -p /tmp/perf-data
echo "📁 Created demo directories: /tmp/tools-demo, /tmp/perf-data"

# Check system resources
echo ""
echo "💾 System Resources:"
echo "Available disk space:"
df -h /
echo ""
echo "Memory info:"
free -h

echo ""
echo "✅ Enhanced Pre-script with Package Installation Completed Successfully"
