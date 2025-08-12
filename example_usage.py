#!/usr/bin/env python3
"""
Example usage of the Merobox CLI
This script demonstrates how to use the CLI programmatically.
"""

import subprocess
import sys
import time

def run_command(command):
    """Run a CLI command and return the result."""
    try:
        result = subprocess.run(
            ['python3', 'merobox_cli.py'] + command.split(),
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        print(f"Error output: {e.stderr}")
        return None

def main():
    """Demonstrate various CLI commands."""
    print("Merobox CLI Examples")
    print("=" * 30)
    
    # Example 1: Show help
    print("\n1. Show help:")
    output = run_command("--help")
    print(output)
    
    # Example 2: Run a single node
    print("\n2. Running a single Calimero node:")
    output = run_command("run")
    print(output)
    
    # Wait a bit for the node to start
    print("\nWaiting 5 seconds for node to start...")
    time.sleep(5)
    
    # Example 3: List running nodes
    print("\n3. List running nodes:")
    output = run_command("list")
    print(output)
    
    # Example 4: Run multiple nodes
    print("\n4. Running multiple Calimero nodes:")
    output = run_command("run --count 2 --base-port 26660")
    print(output)
    
    # Wait a bit for nodes to start
    print("\nWaiting 5 seconds for nodes to start...")
    time.sleep(5)
    
    # Example 5: List all nodes again
    print("\n5. List all running nodes:")
    output = run_command("list")
    print(output)
    
    # Example 6: View logs from first node
    print("\n6. View logs from calimero-node-1:")
    output = run_command("logs calimero-node-1 --tail 10")
    print(output)
    
    # Example 7: Stop a specific node
    print("\n7. Stopping calimero-node-2:")
    output = run_command("stop calimero-node-2")
    print(output)
    
    # Example 8: List nodes after stopping one
    print("\n8. List nodes after stopping one:")
    output = run_command("list")
    print(output)
    
    print("\n" + "=" * 50)
    print("Example completed! You can continue testing with:")
    print("python3 merobox_cli.py --help")

if __name__ == '__main__':
    main()
